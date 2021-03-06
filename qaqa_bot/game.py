# Copyright 2020 Michael Thies
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""
This module provides the business logic / game state transformation for the QAQAgamebot. It is encapsulated in
methods of the `GameServer` class, one for each type of user action. In order to be used, a `GameServer` object needs to
be instantiated with a given bot config and a callback method for sending the messages.

Some of the action methods share code fragments (e.g. for finalizing a game under certain conditions), which have been
un-inlined into some helper functions at the end of the class. Some of them use the `SheetProgressInfo` helper class
to pass around information about sheets.

Additionally, this module defines the `Message` tuple for passing Telegram messages the message sending callback
function and the `@with_session` for magically handling (creating/committing/rolling back) the database sessions.
"""

import datetime
import functools
import gettext
import math
import random
import statistics
import os.path
import logging
from typing import NamedTuple, List, Optional, Iterable, Dict, Any, MutableMapping

import sqlalchemy
import sqlalchemy.exc
from sqlalchemy import func, and_
from sqlalchemy.orm import Session, joinedload, selectinload, raiseload
# We need the MySQLdb driver only to detect Deadlock-Exceptions caused by concurrent modifications.
try:
    import MySQLdb._exceptions
    mysqldb_driver = True
except ImportError:
    mysqldb_driver = False

from . import model
from .util import LazyGetTextBase, GetText, GetNoText, encode_secure_id, NGetText

COMMAND_HELP = "help"
COMMAND_STATUS = "status"
COMMAND_REGISTER = "start"
COMMAND_NEW_GAME = "new_game"
COMMAND_START_GAME = "start_game"
COMMAND_JOIN_GAME = "join_game"
COMMAND_LEAVE_GAME = "leave_game"
COMMAND_STOP_GAME = "stop_game"
COMMAND_STOP_GAME_IMMEDIATELY = "stop_game_immediately"
COMMAND_SET_ROUNDS = "set_rounds"
COMMAND_SET_LANGUAGE = "set_lang"
COMMAND_SET_DISPLAY_NAME = "set_show_name"
COMMAND_SET_SYNC = "set_sync"
COMMAND_SHUFFLE = "shuffle"

LOCALE_DIR = os.path.join(os.path.dirname(__file__), 'i18n')
MAX_TRANSACTION_TRYS = 30

logger = logging.getLogger(__name__)


class Message(NamedTuple):
    """ Representation of an outgoing Telegram message, triggered by some game state change, which still needs to
    be translated with the correct locale for the target chat_id. """
    chat_id: int
    text: LazyGetTextBase


class TranslatedMessage(NamedTuple):
    """ Representation of a (translated) outgoing Telegram message, triggered by some game state change """
    chat_id: int
    text: str


class SheetProgressInfo(NamedTuple):
    """ A helper type, with the relevant information about a single sheet:
    * The sheet itself
    * the number of entries on it and
    * the last of its entries.

    This information may be queried together (e.g. using `_query_sheet_infos()`) and passed between functions to avoid
    superflous SQL queries."""
    sheet: model.Sheet
    num_entries: int
    last_entry: Optional[model.Entry]


def with_session(f):
    """ A decorator for methods of the GameServer class to handle database sessions in a magical way.

    This decorator wraps the GameServer method to create a SQLAlchemy database session from the GameServer's
    sessionmaker before entering the original method. The session is passed to the method as second argument, after
    `self`, before the caller's positional and keyword arguments. The session is committed after the successful
    execution of the method and rolled back in case of an Exception.

    The decorator also handles retries of failed database transactions due to concurrent modifications to the database:
    If such an Exception is detected, the function is re-called with the same parameters up to 30 times. For this to
    work, the wrapped method must be free from side-effects (apart from the changes in the database).
    """
    @functools.wraps(f)
    def wrapper(self: "GameServer", *args, **kwargs):
        session = self.session_maker()
        trys = 0
        while True:
            try:
                result = f(self, session, *args, **kwargs)
                session.commit()
                return result
            except Exception as e:
                session.rollback()
                # If the wrapped function fails with a concurrent database modification, retry the modification.
                if (isinstance(e, sqlalchemy.exc.OperationalError)
                        and mysqldb_driver
                        and isinstance(e.orig, MySQLdb._exceptions.OperationalError)
                        and e.orig.args[0] == 1213):  # MySQL code for "Deadlock detected"
                    # TODO detect similar error of other database backends
                    trys += 1
                    if trys < MAX_TRANSACTION_TRYS:
                        continue
                raise
            finally:
                session.close()
    return wrapper


class GameServer:
    """
    Container for the game state and business logic to change the game state based on interaction events

    A GameServer object holds an SQLAlchemy sessionmaker to create datbase session for handling incoming events.
    Additionally it knows the full configuration (required for some of the messages) and handles sending of outgoing
    messages (which are triggered by incoming events depending on the game state). For this purpose, it is initialized
    with a callback function to be used for sending messages.

    The available game actions are provided as methods of the GameServer object. They should be called by the
    appropriate handlers of the Telegram Bot frontend.

    The GameServer itself is stateless. Since all it member fields are either static (config) or thread-safe
    (_send_callback, session_maker), it is considered to be thread-safe and may be used from thread-pool-executed
    handlers of the Telegram and Web frontends.
    """
    def __init__(self, config: MutableMapping[str, Any],
                 database_engine: Optional[sqlalchemy.engine.Engine] = None):
        """
        Initialize a new

        :param config: The bot configuration, read from the `config.toml` file
        :param send_callback: A callback function taking a list of `Message` tuples and sending them via the Telegram
            API. It should raise an exception when sending fails to trigger the database rollback.
        :param database_engine: (optional) Pre-initialized database engine. The engine should be initialized with
            isolation_level='SERIALIZABLE'. If not given, a new database engine is created, using the
            `database.connection` entry in the config.
        """
        self.config = config

        # database_engine may be given (e.g. for testing purposes). Otherwise, we construct one from the configuration
        if database_engine is None:
            self.database_engine = sqlalchemy.create_engine(config['database']['connection'],
                                                            isolation_level='SERIALIZABLE',
                                                            pool_recycle=config['database'].get('pool_recycle', -1))
        else:
            self.database_engine = database_engine

        # Create database session maker
        self.session_maker = sqlalchemy.orm.sessionmaker(bind=self.database_engine)

    @with_session
    def translate_string(self, session: Session, message: LazyGetTextBase, chat_id: int) -> str:
        """
        Translate a translatable string into the correct language for given chat.

        This message can be used by the `Frontend` to update messages and do other fancy Telegram stuff (which is not
        sending messages) with translated strings.

        This is basically a session-managing wrapper for calling the internal `get_translations()` method a frontend.
        """
        return self._get_translations([Message(chat_id, message)], session)[0].text

    @with_session
    def get_translations(self, session: Session, messages: List[Message]) -> List[TranslatedMessage]:
        """
        Translate a list of messages for the locale of their respective chat_ids.

        This is basically a session-managing wrapper for calling the internal `get_translations()` method a frontend.
        """
        return self._get_translations(messages, session)

    @with_session
    def get_game_result(self, session: Session, game_id: int) -> model.Game:
        game = session.query(model.Game)\
            .filter(model.Game.id == game_id, model.Game.finished != None)\
            .options(raiseload('*'),
                     selectinload(model.Game.participants)
                     .joinedload(model.Participant.user),
                     selectinload(model.Game.sheets)
                     .options(
                         selectinload(model.Sheet.game),
                         selectinload(model.Sheet.entries)
                         .joinedload(model.Entry.user)
                     ))\
            .one_or_none()

        session.expunge_all()
        return game

    @with_session
    def get_game_result_sheet(self, session: Session, sheet_id: int) -> model.Game:
        sheet = session.query(model.Sheet)\
            .filter(model.Sheet.id == sheet_id, model.Game.finished != None)\
            .options(raiseload('*'),
                     joinedload(model.Sheet.game)
                     .selectinload(model.Game.participants)
                     .joinedload(model.Participant.user),
                     selectinload(model.Sheet.entries)
                     .joinedload(model.Entry.user))\
            .one_or_none()

        session.expunge_all()
        return sheet

    @with_session
    def set_chat_locale(self, session: Session, chat_id: int, locale: str, override: bool = False) -> None:
        """
        Set the target locale for outgoing messages for a specific chat id.

        :param locale: A language string like 'de', 'en'
        """
        logger.debug("Setting locale of chat_id %s to %s.", chat_id, locale)
        l = model.SelectedLocale()
        l.chat_id = chat_id
        l.locale = locale
        if override:
            session.merge(l)
        elif session.query(model.SelectedLocale.chat_id)\
                .filter(model.SelectedLocale.chat_id == chat_id)\
                .scalar() is None:
            session.add(l)

    @with_session
    def register_user(self, session: Session, chat_id: int, user_id: int, first_name: str, last_name: str,
                      username: str) -> List[TranslatedMessage]:
        """
        Register a new user and its private chat_id in the database, when they begin a private chat with the
        COMMAND_REGISTER.

        Make sure that the command has been sent in a private chat, before calling this method.

        In case of an already existing user, the user data is updated.

        :param chat_id: The user's private chat id
        :param user_id: The user's Telegram API id
        :param first_name: The Telegram user's first name
        :param last_name: The Telegram user's last name
        :param username: The user's Telegram username, without the leading @ character
        """
        existing_user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        if existing_user is not None:
            existing_user.chat_id = chat_id
            existing_user.first_name = first_name
            existing_user.last_name = last_name
            existing_user.username = username
            logger.info("Updating user %s (%s %s / @%s)", existing_user.id, first_name, last_name, username)  # TODO if username is empty
            return self._get_translations([Message(chat_id, GetText(
                "You are already registered. If you want to start a game, head over to a group chat and spawn a game "
                "with /{cmd}").format(cmd=COMMAND_NEW_GAME))]
                                + self._next_sheet([existing_user], session, repeat=True), session)
        else:
            user = model.User(api_id=user_id, chat_id=chat_id, first_name=first_name, last_name=last_name,
                              username=username)
            session.add(user)
            session.flush()
            logger.info("Created new user %s (%s %s / @%s)", user.id, first_name, last_name, username)  # TODO if username is empty
            return self._get_translations([Message(chat_id, GetText(
                "Hi! I am your friendly qaqa-bot 🤖. \n"
                "I will guide you through hopefully many games of the question-answer-question-answer party game. "
                "Thanks for joining! Now head to the group you want to play the game with and spawn, join and "
                "start a game."))], session)

    @with_session
    def new_game(self, session: Session, chat_id: int, name: str) -> List[TranslatedMessage]:
        """
        Create a new game in the given group chat and inform the group about success or cause of failure of this action.

        Make sure that the chat_id actually belongs to a group chat before calling this method.

        :param name: The game's name. May be the group chat name for simplicity
        """
        running_games = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                         model.Game.finished == None).count()
        if running_games:
            return self._get_translations([Message(chat_id, GetText("Already a running or pending game in this chat"))],
                                          session)  # TODO UX: Add hint to COMMAND_STATUS
        game = model.Game(name=name, chat_id=chat_id, finished=None, started=None, is_waiting_for_finish=False,
                          is_synchronous=True, is_showing_result_names=False)
        session.add(game)
        session.flush()
        logger.info("Created new game %s in chat %s (%s)", game.id, chat_id, name)
        return self._get_translations([Message(chat_id,
                                     GetText("✅ New game created. Use /{command} to join the game.")  # TODO UX: more info
                                     .format(command=COMMAND_JOIN_GAME))], session)

    @with_session
    def set_rounds(self, session: Session, chat_id: int, rounds: int) -> List[TranslatedMessage]:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id, GetText("❌ No game to configure in this chat"))], session)  # TODO UX: Add hint to COMMAND_NEW_GAME
        if game.started is not None:
            return self._get_translations(
                [Message(chat_id, GetText("❌ Sorry, I can only configure a game before its start. ⏳"))], session)
            # TODO allow rounds change for running games (unless a sheet has > rounds * entries). In this case, sheets with
            #  len(entries) = old_rounds may require to be newly assigned
        if rounds < 1:
            return self._get_translations([Message(chat_id, GetText("invalid rounds number. Must be &gt;= 1"))], session)
        logger.info("Setting rounds of game %s to %s", game.id, rounds)
        game.rounds = rounds
        return self._get_translations([Message(chat_id, GetText(
            "Number of rounds set: {number_rounds}").format(number_rounds=game.rounds))], session)

    @with_session
    def set_synchronous(self, session: Session, chat_id: int, state: bool) -> List[TranslatedMessage]:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id, GetText("No game to configure in this chat"))], session)  # TODO UX
        if game.started is not None:
            return self._get_translations([Message(chat_id, GetText(
                "❌ Sorry, I can only configure a game before its start. ⏳"))], session)
            # TODO allow mode change for running games (requires passing of waiting sheets for sync → unsync)
        logger.info("Setting game %s to %s", game.id, "synchronous" if state else "asynchronous")
        game.is_synchronous = state
        return self._get_translations([Message(chat_id, GetText(f"✅ Set game mode."))], session)

    @with_session
    def set_show_result_names(self, session: Session, chat_id: int, state: bool) -> List[TranslatedMessage]:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id, GetText("❌ No game to configure in this chat"))], session)  # TODO UX
        if game.started is not None:
            return self._get_translations([Message(chat_id, GetText(
                "❌ Sorry, I can only configure a game before its start. ⏳"))], session)
            # TODO should this be possible?
        logger.info("Setting game %s to %s", game.id, "show result names" if state else "not show result names")
        game.is_showing_result_names = state
        return self._get_translations([Message(chat_id, GetNoText("✅"))], session)

    @with_session
    def join_game(self, session: Session, chat_id: int, user_id: int) -> List[TranslatedMessage]:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.finished == None)\
            .one_or_none()
        if game is None:
            return self._get_translations(
                [Message(chat_id, GetText("There is currently no pending game in this group. 🙃 Use /{command} to "
                                          "create one.").format(command=COMMAND_NEW_GAME))],
                session)
        user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        if user is None:
            return self._get_translations([
                Message(chat_id, GetText("You must start a chat with the bot first. Use the following link: "
                                         "https://t.me/{bot_name}?{command}=now and click \"START\"\n"
                                         "Afterwards, come back here and use /{command_join} again.")
                        .format(bot_name=self.config['bot']['username'], command=COMMAND_REGISTER,
                                command_join=COMMAND_JOIN_GAME))],
                session)
        existing_participations = session.query(model.Participant)\
            .filter(model.Participant.game == game, model.Participant.user == user)\
            .count()
        if existing_participations:
            logger.info("User %s has already joined game %s before", user.id, game.id)
            return []

        new_sheet = False
        if game.started is not None:
            # Joining into running games ist only allowed for asynchronous games or in the first round of a synchronous
            # game
            sheet_infos = self._game_sheet_infos(game, session)
            if game.is_synchronous:
                if any(si.num_entries == 0 for si in sheet_infos):
                    new_sheet = True
                    logger.info("User %s joins running synchronous game %s in first round", user.id, game.id)
                else:
                    logger.info("User %s cannot join %s, which is already started synchronously.", user.id, game.id)
                    return self._get_translations([Message(chat_id, GetText("⏳ Oh no! The game has already started! "
                                                                            "Please join the next game."))], session)
            else:
                # Add a new sheet if other sheets have only few entries (< ¼ of target rounds), too.
                if min((si.num_entries for si in sheet_infos), default=0) < game.rounds // 4:
                    new_sheet = True
                logger.info("User %s joins running asynchronous game %s %s new sheet", user.id, game.id,
                            "with" if new_sheet else "without")
        else:
            logger.info("User %s joins to game %s", user.id, game.id)
        game.participants.append(model.Participant(user=user))
        messages = [Message(chat_id, GetText("Yay! Welcome {name} 🤗").format(name=user.first_name))]

        if new_sheet:
            user.pending_sheets.append(model.Sheet(game=game))
            messages.extend(self._next_sheet([user], session))
        return self._get_translations(messages, session)

    @with_session
    def start_game(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.finished == None)\
            .one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id,
                                                   GetText("There is currently no pending game in this Group. "
                                                           "Use /{command} to start one.")
                                                   .format(command=COMMAND_NEW_GAME))],
                                          session)
        elif game.started is not None:
            return self._get_translations([Message(chat_id, GetText("The game is already running"))], session)  # TODO refer to /status command
        elif len(game.participants) < 2:
            logger.debug("Game %s has not enough participants to be started", game.id)
            return self._get_translations([Message(chat_id, GetText(
                "No games with less than two participants permitted 🙅‍♀️"))], session)

        # Create sheets and start game
        for participant in game.participants:
            participant.user.pending_sheets.append(model.Sheet(game=game))
        game.started = datetime.datetime.now(datetime.timezone.utc)

        # Set number of rounds if unset
        if game.rounds is None:
            game.rounds = calculate_preset_rounds(len(game.participants))
            logger.debug("Setting game %s's rounds automatically to %s", game.id, game.rounds)

        logger.info("Starting game %s", game.id)
        # Give sheets to participants
        result = [Message(chat_id, GetNoText("Let's go!")), Message(chat_id, GetNoText("📝"))]
        result.extend(self._next_sheet([p.user for p in game.participants], session))
        return self._get_translations(result, session)

    @with_session
    def leave_game(self, session: Session, chat_id: int, user_id: int) -> List[TranslatedMessage]:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.finished == None)\
            .one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id,
                                           GetText("There is currently no running or pending game in this chat."))],
                                session)
        num_participants = session.query(func.count(model.Participant.user_id))\
            .filter(model.Participant.game == game)\
            .scalar()

        if num_participants <= 2 and game.started is not None:
            logger.debug("Cannot remove user from game %s, since they are one of 2 or less remaining participants",
                         game.id)
            return self._get_translations([Message(chat_id, GetText("You are one of the last two participants of this "
                                                                    "game. Thus, you cannot leave."))], session)

        # Remove user as participant from game
        user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        participation = session.query(model.Participant)\
            .filter(model.Participant.user == user, model.Participant.game == game)\
            .one_or_none()
        if participation is None:
            logger.debug("Cannot remove user %s from game %s, since they do not participate. 🤨", user.id, game.id)
            return self._get_translations([Message(chat_id, GetText("You didn't participate in this game."))], session)
        session.delete(participation)

        result = [Message(chat_id, GetText("👋 Bye!"))]
        logger.info("User %s leaves %sgame %s.", user.id, "running " if game.started is not None else "", game.id)

        # Pass on pending sheets
        if user.current_sheet is not None and user.current_sheet.game_id == game.id:
            logger.debug("Retracting sheet %s from user %s, who left the game.", user.current_sheet_id, user.id)
            result.append(Message(user.chat_id, GetText("You left the game. No answer required anymore.")))
            result.extend(self._next_sheet([user], session))
        obsolete_sheets = [sheet
                           for sheet in user.pending_sheets
                           if sheet.game_id == game.id]
        logger.debug("Passing sheets %s from user %s, who left the game.",
                     ",".join(str(s.id) for s in obsolete_sheets), user.id)
        self._assign_sheet_to_next(obsolete_sheets, game, session)
        result.extend(self._next_sheet([sheet.current_user for sheet in obsolete_sheets if sheet.current_user], session))
        return self._get_translations(result, session)

    @with_session
    def stop_game(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        """
        Handle a request for a normal game stop in the given group chat.

        This method sets the `Game.is_waiting_for_finish` attribute of the group's current `Game` and checks if the
        stop condition (each sheet ends with an answer) is already satisfied, using
        `_finish_if_stopped_and_all_answered()`. In this case the game is finalized.
        """
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.started != None,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id,
                                                   GetText("There is currently no running game in this group."))],
                                          session)

        logger.info("Marking game %s to stop at next opportunity.", game.id)
        game.is_waiting_for_finish = True
        sheet_infos = list(self._game_sheet_infos(game, session, eager_current_user=True))

        messages = self._finish_if_stopped_and_all_answered(game, sheet_infos, session)

        if game.finished is None:
            logger.info("Retracting answered sheets of game %s to accelerate end of game.", game.id)
            # Retract sheets that do not end with a question (i.e. remove from users' stacks and inform user if it is
            # their current_sheet)
            users_to_update = set()
            for sheet_info in sheet_infos:
                if not sheet_info.num_entries or sheet_info.last_entry.type == model.EntryType.ANSWER:
                    sheet_user: Optional[model.User] = sheet_info.sheet.current_user
                    if sheet_user is not None:
                        logger.debug("Removing sheet %s from user %s's queue due to game stop.",
                                     sheet_info.sheet.id, sheet_user.id)
                        sheet_info.sheet.current_user = None
                        if sheet_user.current_sheet == sheet_info.sheet:
                            logger.debug("Retracting sheet %s from user %s due to game stop.",
                                         sheet_info.sheet.id, sheet_user.id)
                            sheet_user.current_sheet = None
                            messages.append(Message(sheet_user.chat_id,
                                                    GetText("Game will be stopped. No new question required anymore.")))
                            users_to_update.add(sheet_user)
            messages.extend(self._next_sheet(list(users_to_update), session))

        return self._get_translations(messages, session)

    @with_session
    def immediately_stop_game(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        """
        Handle a request for an immediate game stop in the given group chat.

        The game is finalized (retracts pending sheets and sends result messages) using `_finalize_game()`.
        """
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.started != None,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations([Message(chat_id,
                                                   GetText("There is currently no running game in this group."))],
                                session)
        logger.info("Immediately stopping game %s.", game.id)
        return self._get_translations(self._finalize_game(game, session), session)

    @with_session
    def submit_text(self, session: Session, chat_id: int, message_id: int, text: str) -> List[TranslatedMessage]:
        """
        Process a message send by a user in their private chat.

        This method may trigger a lot of messages: The user may get request for a submission to the next sheet, another
        user may get the submitted text (or even all participants of the game if a new round of a synchronous game is
        triggered), and/or the game is finalized and result messages are generated.

        :param chat_id: The chat_id in which the message has been received. Should be the private chat with a user.
        :param message_id: Telegram's message id (within the chat). It is stored in the database and used to identify
            the message on edits.
        :param text: The messages text.
        """
        user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
        if user is None:
            return self._get_translations([Message(chat_id,
                                                   GetText("Unexpected message. Please use /{command} to register with "
                                                           "the bot.").format(command=COMMAND_REGISTER))],
                                          session)
        current_sheet = user.current_sheet
        if current_sheet is None:
            logger.debug("Got unexpected text message from user %s (chat_id %s).", user.id, chat_id)
            return self._get_translations([Message(chat_id, GetText("Unexpected message."))], session)

        result = []

        # Create new entry
        logger.info("Adding entry by user %s to sheet %s in game %s.",
                    user.id, current_sheet.id, current_sheet.game.id)
        entry_type = (model.EntryType.QUESTION
                      if not current_sheet.entries or current_sheet.entries[-1].type == model.EntryType.ANSWER
                      else model.EntryType.ANSWER)
        current_sheet.entries.append(model.Entry(user=user, text=text, type=entry_type, chat_id=chat_id,
                                                 message_id=message_id,
                                                 timestamp=datetime.datetime.now(datetime.timezone.utc)))
        result.append(Message(chat_id, GetNoText("🆗")))
        user.current_sheet = None
        current_sheet.current_user = None

        # Check if game is finished
        game = current_sheet.game
        sheet_infos = list(self._game_sheet_infos(game, session))
        result.extend(self._finish_if_complete(game, sheet_infos, session))
        result.extend(self._finish_if_stopped_and_all_answered(game, sheet_infos, session))

        if game.finished is None and len(current_sheet.entries) < game.rounds:
            # In a synchronous game: Check if the round is finished and pass sheets on
            if game.is_synchronous:
                logger.debug("Checking if new round in synchronous game %s should be triggered.", game.id)
                if all(num_entries == len(current_sheet.entries) for sheet, num_entries, last_entry in sheet_infos):
                    logger.info("Triggering new round %s in synchronous game %s.",
                                len(current_sheet.entries) + 1, game.id)
                    # TODO don't assign answered sheets in stopped games? Might be relevant for leaving/joining in-game
                    self._assign_sheet_to_next(list(game.sheets), game, session)
                    result.extend(self._next_sheet(list(p.user for p in game.participants), session))

            # In an asynchronous game: Pass on this sheet
            elif not game.is_waiting_for_finish or entry_type == model.EntryType.QUESTION:
                self._assign_sheet_to_next([current_sheet], game, session)
                assert(current_sheet.current_user is not None)
                result.extend(self._next_sheet([current_sheet.current_user], session))

        result.extend(self._next_sheet([user], session))
        return self._get_translations(result, session)

    @with_session
    def edit_submitted_message(self, session: Session, chat_id: int, message_id: int, new_text: str) \
            -> List[TranslatedMessage]:
        entry = session.query(model.Entry)\
            .filter(model.Entry.chat_id == chat_id, model.Entry.message_id == message_id)\
            .options(joinedload(model.Entry.sheet).joinedload(model.Sheet.game))\
            .one_or_none()

        if entry is None:
            # TODO message?
            return []

        if entry.sheet.game.finished is not None:
            return self._get_translations([Message(chat_id, GetText("Changing message “{old_text}” is not accepted, "
                                                                    "because the relevant game is already finished.")
                                                   .format(old_text=truncate_string(entry.text)))], session)

        last_entry_pos = session.query(func.max(model.Entry.position))\
            .filter(model.Entry.sheet_id == entry.sheet_id)\
            .scalar()
        if entry.position < last_entry_pos:
            return self._get_translations([Message(chat_id,
                                                   GetText("Changing message “{old_text}” is not accepted, "
                                                           "because the next player already responded to that entry.")
                                                   .format(old_text=truncate_string(entry.text)))], session)

        result = [Message(chat_id, GetText("🆗 Change to message “{old_text}” was accepted.")
                          .format(old_text=truncate_string(entry.text, 100)))]
        entry.text = new_text
        logger.info("Latest entry on sheet %s was edited.", entry.sheet_id)

        current_user = session.query(model.User).filter(model.User.current_sheet_id == entry.sheet.id).one_or_none()
        if current_user is not None:
            logger.debug("Informing user %s about edit on sheet %s.", current_user.id, entry.sheet_id)
            result.append(Message(current_user.chat_id, GetText("The {type} has been updated by its author:")
                                  .format(type=(GetText("question")
                                                if entry.type == model.EntryType.QUESTION
                                                else GetText("answer")))))
            result.extend(self._next_sheet([current_user], session, repeat=True))
        return self._get_translations(result, session)

    # TODO resend_current_sheet()

    @with_session
    def get_user_status(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        """
        Send infos about the current state (games, pending sheets) to a user.

        Make sure that the chat_id actually belongs to a private chat before calling this method.
        """
        user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
        if user is None:
            return self._get_translations(
                [Message(chat_id, GetText("You are currently not registered for using this bot. Please use "
                                          "/{command} to register with the bot.").format(command=COMMAND_REGISTER))],
                session)

        # Inform about game participations
        running_games = [p.game for p in user.participations if p.game.started is not None and p.game.finished is None]
        pending_games = [p.game for p in user.participations if p.game.started is None and p.game.finished is None]

        parts = []
        if running_games:
            parts.append(GetText("You are currently participating in the following games: {games}")
                         .format(games=GetText(', ').join(g.name for g in running_games)))
            if pending_games:
                parts.append(GetText("Additionally, you will be participating in {games}, as soon as they start.")
                             .format(games=GetText(', ').join(g.name for g in pending_games)))
        elif pending_games:
            parts.append(GetText("You will be participating in the follwing games, as soon as they start: {games}")
                         .format(games=', '.join(g.name for g in pending_games)))
        else:
            parts.append(GetText("You are currently not participating in any QAQA game."))

        # Inform about pending sheets
        if running_games:
            if user.pending_sheets:
                # TODO use NGetText for pluralizing sheets
                parts.append(GetText("\nYou have currently {num_sheets} pending sheets to ask or answer "
                                     "questions, including the current one.")
                             .format(num_sheets=len(user.pending_sheets)))
            else:
                parts.append(GetText("\nYou have currently no pending sheets ✨"))

        status = GetNoText("\n").join(parts)
        return self._get_translations([Message(chat_id, status)] + self._next_sheet([user], session, repeat=True),
                                      session)

    @with_session
    def get_group_status(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        """
        Send infos about the current game state (current running/pending game, players, sheets, entries) to a group.

        Make sure that the chat_id actually belongs to a group chat before calling this method.
        """
        current_game: model.Game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                                    model.Game.finished == None).one_or_none()
        if current_game is None:
            status = GetText("There is currently no QAQA-game in this group. Use /{command} to start one.")\
                .format(command=COMMAND_NEW_GAME)
        else:
            players = [p.user for p in current_game.participants]
            players_text = (GetNoText("• ") + GetNoText('\n• ').join(u.format_name() for u in players)
                            if current_game.participants
                            else GetText("– none –"))
            configuration = GetText("Rounds: {num_rounds}\nSynchronous: {synchronous}")\
                .format(num_rounds=(GetText('{number} (based on no. of players)')
                                    .format(number=calculate_preset_rounds(len(players)))
                                    if current_game.rounds is None
                                    else current_game.rounds),
                        synchronous=GetText('yes') if current_game.is_synchronous else GetText('no'))
            sheet_infos = self._game_sheet_infos(current_game, session, eager_current_user=True)
            sheets_stats = (
                GetText(" They have {min}–{max} (Median: {median}) entries yet.")
                .format(min=min(si.num_entries for si in sheet_infos),
                        max=max(si.num_entries for si in sheet_infos),
                        median=statistics.median(si.num_entries for si in sheet_infos))
                if sheet_infos else "")
            pending_sheets = [si.sheet for si in sheet_infos if si.sheet.current_user_id is not None]
            pending_users = (
                GetText("We are currently waiting for {users} 👀\n\n")
                .format(users=', '.join(s.current_user.format_name(True, players) for s in pending_sheets))
                if current_game.is_synchronous or len(pending_sheets) <= len(sheet_infos) / 3
                else "")
            if current_game.started is not None:
                status = GetText("The game is on! 👾\n\n"
                                 "{trans_num_sheets} in the game.{sheets_stats}\n\n"
                                 "{pending_users}"
                                 "{trans_reg_players}:\n"
                                 "{players}\n\n"
                                 "Game configuration:\n{configuration}")\
                    .format(trans_num_sheets=NGetText('One sheet is', '{n} sheets are', len(sheet_infos))
                            .format(n=len(sheet_infos)),
                            sheets_stats=sheets_stats, pending_users=pending_users,
                            trans_reg_players=NGetText('Registered player', 'Registered players ({number})',
                                                       len(players))
                                              .format(number=len(players)),
                            players=players_text, configuration=configuration)
            else:
                status = GetText("The game has been created and waits to be started. 🕰\n"
                                 "Use /{command} to start the game.\n\n"
                                 "{trans_reg_players}:\n"
                                 "{players}\n\n"
                                 "Game configuration:\n{configuration}")\
                    .format(command=COMMAND_START_GAME, players=players_text, configuration=configuration,
                            trans_reg_players=NGetText('Registered player', 'Registered players ({number})',
                                                       len(players))
                                              .format(number=len(players)))
        return self._get_translations([Message(chat_id, status)], session)

    @with_session
    def shuffle_players(self, session: Session, chat_id: int) -> List[TranslatedMessage]:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.finished == None).one_or_none()
        if game is None:
            return self._get_translations(
                [Message(chat_id, GetText("There is currently no running game in this group."))], session)
        if not game.participants:
            return self._get_translations(
                [Message(chat_id, GetText("There are currently no players to shuffle."))], session)

        random.shuffle(game.participants)

        players_text = GetNoText("• ") + GetNoText('\n• ').join(p.user.format_name() for p in game.participants)
        return self._get_translations(
            [Message(chat_id, GetText("🆗 New player order is:\n{players}").format(players=players_text))], session)

    # ###########################################################################
    # Helper methods for translating messages

    def _get_translations(self, messages: List[Message], session: Session) -> List[TranslatedMessage]:
        """
        Helper function to look up a the target language for a list of `Message`s and translate them.
        """
        locales = dict(session.query(model.SelectedLocale.chat_id, model.SelectedLocale.locale)
                       .filter(model.SelectedLocale.chat_id.in_(set(m.chat_id for m in messages)))
                       .all())
        return [TranslatedMessage(m.chat_id,
                                  m.text.get_translation(gettext.translation('qaqa_bot',
                                                                             LOCALE_DIR,
                                                                             (locales.get(m.chat_id, 'en'),),
                                                                             fallback=True)))
                for m in messages]

    # ###########################################################################
    # Helper methods for managing sheets

    def _game_sheet_infos(self, game: model.Game, session: Session, eager_current_user: bool = False
                          ) -> List[SheetProgressInfo]:
        """ Helper function to get the `SheetProgressInfo` for all sheets of a given Game.

        This list is required by some of the helper functions below. It is generated with an optimized SQL query and may
        be used multiple times, e.g. for checking if a game is finished by `_finish_if_complete` and
        `_finish_if_stopped_and_all_answered`.

        :param eager_current_user: If True, the Sheet.current_user field is loaded eagerly (using Joined Eager Loading)
        """
        num_subquery = session.query(model.Entry.sheet_id,
                                     func.count().label('num_entries')) \
            .group_by(model.Entry.sheet_id) \
            .subquery()
        max_pos_subquery = session.query(model.Entry.sheet_id,
                                         func.max(model.Entry.position).label('max_position')) \
            .group_by(model.Entry.sheet_id) \
            .subquery()
        query = session.query(model.Sheet, num_subquery.c.num_entries, model.Entry)\
            .outerjoin(num_subquery, model.Sheet.id == num_subquery.c.sheet_id)\
            .outerjoin(max_pos_subquery)\
            .outerjoin(model.Entry, and_(model.Entry.sheet_id == model.Sheet.id,
                                         model.Entry.position == max_pos_subquery.c.max_position)) \
            .filter(model.Sheet.game == game)
        if eager_current_user:
            query = query.options(joinedload(model.Sheet.current_user))

        return [SheetProgressInfo(sheet, num_entries if num_entries is not None else 0, last_entry)
                for sheet, num_entries, last_entry
                in query]

    def _next_sheet(self, users: Iterable[model.User], session: Session, repeat: bool = False) -> List[Message]:
        """ Helper function to check for a list of users, if they have no current sheet, pick the next sheet from their
        pending stack and generate messages to them to ask for their next submission.

        :param users: The users to check for pending sheets and send messages to
        :param repeat: If True, resend the request for submission to all given users, even if they already have a sheet
            assigned."""
        # The following manually crafted SQL query is basically and extended version of _game_sheet_infos() to
        # efficiently query sheets, their entry numbers and last entries along with each User object.
        min_sheet_pos_subquery = session.query(model.Sheet.current_user_id,
                                               func.min(model.Sheet.pending_position).label('min_position')) \
            .group_by(model.Sheet.current_user_id) \
            .subquery()
        num_subquery = session.query(model.Entry.sheet_id,
                                     func.count().label('num_entries')) \
            .group_by(model.Entry.sheet_id) \
            .subquery()
        max_pos_subquery = session.query(model.Entry.sheet_id,
                                         func.max(model.Entry.position).label('max_position')) \
            .group_by(model.Entry.sheet_id) \
            .subquery()
        query = session.query(model.User,
                              model.Sheet,
                              num_subquery.c.num_entries,
                              model.Entry)\
            .outerjoin(min_sheet_pos_subquery)\
            .outerjoin(model.Sheet, and_(model.Sheet.current_user_id == model.User.id,
                                         model.Sheet.pending_position == min_sheet_pos_subquery.c.min_position))\
            .outerjoin(num_subquery, model.Sheet.id == num_subquery.c.sheet_id)\
            .outerjoin(max_pos_subquery)\
            .outerjoin(model.Entry, and_(model.Entry.sheet_id == model.Sheet.id,
                                         model.Entry.position == max_pos_subquery.c.max_position))\
            .filter(model.User.id.in_(u.id for u in users))

        result = []
        logger.debug("Checking %s to users %s.",
                     "current sheet to be processed" if repeat else "if a new sheets should be passed",
                     ",".join(str(u.id) for u in users))
        for user, next_sheet, next_sheet_num_entries, next_sheet_last_entry in query:
            if (user.current_sheet_id is None or repeat) and next_sheet is not None:
                user.current_sheet = next_sheet
                logger.debug("Giving sheet %s to user %s.", next_sheet.id, user.id)
                result.append(Message(user.chat_id, self._format_for_next(
                    SheetProgressInfo(next_sheet,
                                      next_sheet_num_entries if next_sheet_num_entries is not None else 0,
                                      next_sheet_last_entry),
                    user.current_sheet_id is not None)))
        return result

    def _format_for_next(self, sheet_info: SheetProgressInfo, repeat: bool) -> LazyGetTextBase:
        """ Create the message content for showing a sheet to a user and ask them for their next submission. The message
        contains the last entry of the sheet or a request to write the initial question if the sheet is empty.

        :param repeat: True, if this a repeated message for the same sheet and user"""
        if sheet_info.num_entries == 0:
            return GetText("Please ask a question to begin a new sheet for game <i>{game_name}</i>.")\
                .format(game_name=sheet_info.sheet.game.name)
        else:
            assert(sheet_info.last_entry is not None)
            if sheet_info.last_entry.type == model.EntryType.ANSWER:
                return GetText("Please ask a question that may be answered with:\n“{text}”")\
                    .format(text=sheet_info.last_entry.text)
            else:
                return GetText("Please answer the following question:\n“{text}”")\
                    .format(text=sheet_info.last_entry.text)

    def _assign_sheet_to_next(self, sheets: List[model.Sheet], game: model.Game, session: Session):
        """ Assign a list of sheets of a single game to the next user according the game's participant order

        This may be used with a list of all sheets of the game to begin a new round in a synchronous game or a single
        sheet for submissions in an asynchronous game.

        The list of sheets is processed at once for optimization reasons: The order of game participants is generated
        only once and an optimized query is used to fetch all required information about the sheets' entries at once."""
        next_mapping = {p1.user_id: p2.user for p1, p2 in zip(game.participants, game.participants[1:])}
        next_mapping[game.participants[-1].user_id] = game.participants[0].user

        # Fetch the last entry of all sheets with a single query. It is basically a manual version of SQLAlchemy's
        # `selectinload`
        max_pos_subquery = session.query(model.Entry.sheet_id,
                                         func.max(model.Entry.position).label('max_position')) \
            .group_by(model.Entry.sheet_id)\
            .subquery()
        query = session.query(model.Sheet.id, model.Entry)\
            .outerjoin(max_pos_subquery)\
            .outerjoin(model.Entry, and_(model.Entry.sheet_id == model.Sheet.id,
                                         model.Entry.position == max_pos_subquery.c.max_position))\
            .filter(model.Sheet.id.in_(sheet.id for sheet in sheets))
        last_entry_by_sheet_id: Dict[int, model.Entry] = dict(query.all())

        for sheet in sheets:
            sheet.current_user = None
            sheet.pending_position = None
            if last_entry_by_sheet_id[sheet.id] is None:
                # Passing on an empty sheet, means something unusual happend (e.g. the user left the game before writing
                # something). Let's get rid of those sheets.
                session.delete(sheet)
                continue

            next_user = next_mapping[last_entry_by_sheet_id[sheet.id].user_id]
            logger.debug("Assigning sheet %s to user %s ...", sheet.id, next_user.id)
            next_user.pending_sheets.append(sheet)

    # ###########################################################################
    # Helper methods for ending the game

    def _finish_if_complete(self, game: model.Game, sheet_infos: Iterable[SheetProgressInfo],
                            session: Session) -> List[Message]:
        """ Finalize the game if it is completed (i.e. all sheets have the number entries).

        This function uses `_finalize_game()` to generate the result messages in this case."""
        logger.debug("Checking game %s for completeness ...", game.id)
        if all(sheet_info.num_entries >= game.rounds for sheet_info in sheet_infos):
            return self._finalize_game(game, session)
        return []


    def _finish_if_stopped_and_all_answered(self, game: model.Game, sheet_infos: Iterable[SheetProgressInfo],
                                            session: Session) -> List[Message]:
        """
        Finish the game if it is waiting for finishing and the finishing condition (each page ends with an answer).

        :param game: The Game object
        :param sheet_infos: A list of SheetProgressInfo for *all* sheets of the game, as retrieved from
            `_game_sheet_infos()`
        """
        if game.is_waiting_for_finish:
            logger.debug("Checking if opportunity to stop game %s is give n...", game.id)
            if all(sheet_info.last_entry is None or sheet_info.last_entry.type == model.EntryType.ANSWER
                   for sheet_info in sheet_infos):
                return self._finalize_game(game, session)
        return []

    def _finalize_game(self, game: model.Game, session: Session) -> List[Message]:
        """ Finalize a game: Collect pending sheets (inform users that no answer is required anymore) and generate
        result messages to the game's group chat."""
        logger.info("Finalizing game %s ...", game.id)
        messages = []
        # Requery sheets with optimized loading of current_user
        # TODO this is only required when called via `.stop_game_immediately()`. In all other cases, the sheets have
        #  already been called with a joinedload of the Sheet.current_user.
        sheets: List[model.Sheet] = session.query(model.Sheet)\
            .filter(model.Sheet.game == game)\
            .populate_existing()\
            .options(joinedload(model.Sheet.current_user))\
            .all()

        # Reset pending sheets
        users_to_update = set()
        for sheet in sheets:
            sheet_user: Optional[model.User] = sheet.current_user
            if sheet_user is not None:
                sheet.current_user = None
                if sheet_user.current_sheet == sheet:
                    sheet_user.current_sheet = None
                    logger.debug("Retracting sheet %s from user %s due to finalized game.", sheet.id, sheet_user.id)
                    messages.append(Message(sheet_user.chat_id, GetText("Game was ended. No answer required anymore.")))
                    users_to_update.add(sheet_user)
        messages.extend(self._next_sheet(list(users_to_update), session))

        # Generate result URL message
        locale = session.query(model.SelectedLocale.locale)\
            .filter(model.SelectedLocale.chat_id == game.chat_id)\
            .scalar()
        locale = locale or 'en'
        messages.append(
            Message(game.chat_id, GetText("Game finished. View results at <a href=\"{url}\">{url}</a>.").format(
                url="{}/game/{}/?lang={}{}".format(
                    self.config['web']['base_url'],
                    encode_secure_id(game.id, self.config['secret'],
                                     b'game+' if game.is_showing_result_names else b'game'),
                    locale,
                    "&authors=1" if game.is_showing_result_names else ""))))
        game.finished = datetime.datetime.now(datetime.timezone.utc)
        return messages

    def _entry_to_string(self, game: model.Game, entry: model.Entry) -> str:
        if game.is_showing_result_names:
            return f"\n{entry.user.format_name(True, (p.user for p in game.participants))}: {entry.text}"
        else:
            return "\n" + entry.text

    def _format_result(self, game: model.Game, sheet: model.Sheet) -> List[LazyGetTextBase]:
        """ Serialize a finished sheet to a list of strings to be sent as result message when finalizing a game. """
        messages = [GetNoText("❓❕  ❔❗️  ⁉️  ‼️")]
        msg = ""
        for entry in sheet.entries:
            # size 4096 is defined by the telegram API as maximal message length
            entry_str = self._entry_to_string(game, entry)
            if len(msg) + len(entry_str) < 4096:
                msg += entry_str
            else:
                messages.append(GetNoText(msg))
                msg = entry_str
        messages.append(GetNoText(msg))
        return messages
        # TODO UX: improve


def truncate_string(s, length=200, end="…"):
    """ A simplified version of Jinja2's truncate filter. """
    if len(s) <= length:
        return s
    result = s[: length - len(end)].rsplit(" ", 1)[0]
    return result + end


def calculate_preset_rounds(player_number):
    """
    Calculation function for the number of rounds preset, based on the number of players

    :param player_number: Number of players in the game
    :return: Preset number of rounds of the game
    """
    return max(6, math.floor(player_number / 2) * 2)
