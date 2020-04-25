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
un-inlined into some helper functions at the bottom of the file.

Additionally, this module defines the `Message` tuple for passing Telegram messages the message sending callback
function and the `@with_session` for magically handling (creating/committing/rolling back) the database sessions.
"""

import datetime
import functools
import gettext
import math
import statistics
import os.path
import logging
from typing import NamedTuple, List, Optional, Iterable, Dict, Any, Callable, MutableMapping

import sqlalchemy
from sqlalchemy import func, and_
from sqlalchemy.orm import Session, joinedload

from . import model
from .util import LazyGetTextBase, GetText, NGetText, GetNoText

COMMAND_HELP = "help"
COMMAND_STATUS = "status"
COMMAND_REGISTER = "start"
COMMAND_NEW_GAME = "new_game"
COMMAND_START_GAME = "start_game"
COMMAND_JOIN_GAME = "join_game"
COMMAND_STOP_GAME = "stop_game"
COMMAND_STOP_GAME_IMMEDIATELY = "stop_game_immediately"
COMMAND_SET_ROUNDS = "set_rounds"
COMMAND_SET_LANGUAGE = "set_lang"
COMMAND_SET_DISPLAY_NAME = "set_show_name"
COMMAND_SET_SYNC = "set_sync"

LOCALE_DIR = os.path.join(os.path.dirname(__file__), 'i18n')

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


def with_session(f):
    """ A decorator for methods of the GameServer class to handle database sessions in a magical way.

    This decorator wraps the GameServer method to create a SQLAlchemy database session from the GameServer's
    sessionmaker before entering the original method. The session is passed to the method as second argument, after
    `self`, before the caller's positional and keyword arguments. The session is committed after the successful
    execution of the method and rolled back in case of an Exception. """
    @functools.wraps(f)
    def wrapper(self: "GameServer", *args, **kwargs):
        session = self.session_maker()
        try:
            result = f(self, session, *args, **kwargs)
            session.commit()
            return result
        except Exception:
            session.rollback()
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

    The available game actions are provided as methods of the GameState object. They should be called by the appropriate
    handlers of the Telegram Bot frontend.
    """
    def __init__(self, config: MutableMapping[str, Any],
                 send_callback: Callable[[List[TranslatedMessage]], None],
                 database_engine: Optional[sqlalchemy.engine.Engine] = None):
        """
        Initialize a new

        :param config: The bot configuration, read from the `config.toml` file
        :param send_callback: A callback function taking a list of `Message` tuples and sending them via the Telegram
            API. It should raise an exception when sending fails to trigger the database rollback.
        :param database_engine: (optional) Pre-initialized database engine. If not given, a new database engine is
            created, using the `database.connection` entry in the config.
        """
        self.config = config
        self._send_callback = send_callback

        # database_engine may be given (e.g. for testing purposes). Otherwise, we construct one from the configuration
        if database_engine is None:
            self.database_engine = sqlalchemy.create_engine(config['database']['connection'])
        else:
            self.database_engine = database_engine

        # Create database session maker
        self.session_maker = sqlalchemy.orm.sessionmaker(bind=self.database_engine)

    @with_session
    def send_messages(self, session: Session, messages: List[Message]) -> None:
        """
        Send a list of translatable messages.

        For each message, the target locale is looked up in the database according to the target chat_id, and the
        message is translated for that locale and sent to the chat.

        This message can be used by the `Frontend` to send a direct response message (without further interaction with
        the GameServer) in the correct language.
        """
        self._send_messages(messages, session)

    @with_session
    def get_translation(self, session: Session, message: Message) -> TranslatedMessage:
        """
        Translate a translatable `Message` into the correct language for the target chat.

        This message can be used by the `Frontend` to update messages and do other fancy Telegram stuff (which is not
        sending messages) with translated strings.
        """
        return _get_translations([message], session)[0]

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
    def register_user(self, session: Session, chat_id: int, user_id: int, user_name: str) -> None:
        """
        Register a new user and its private chat_id in the database, when they begin a private chat with the
        COMMAND_REGISTER.

        Make sure that the command has been sent in a private chat, before calling this method.

        In case of an already existing user, the user data is updated.

        :param chat_id: The user's private chat id
        :param user_id: The user's Telegram API id
        :param user_name: The user's name: Either the Telegram username (including an '@' prefix) or otherwise their
            first name
        """
        existing_user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        if existing_user is not None:
            existing_user.chat_id = chat_id
            existing_user.name = user_name
            logger.info("Updating user %s (%s)", existing_user.id, user_name)
            self._send_messages([Message(chat_id, GetText(
                "You are already registered. If you want to start a game, head over to a group chat and spawn a game "
                "with /{cmd}").format(cmd=COMMAND_NEW_GAME))]  # TODO UX
                                + _next_sheet([existing_user], session, repeat=True), session)
        else:
            user = model.User(api_id=user_id, chat_id=chat_id, name=user_name)
            session.add(user)
            logger.info("Created new user %s (%s)", user.id, user_name)
            self._send_messages([Message(chat_id, GetText(
                "Hi! I am your friendly qaqa-bot 🤖. \n"
                "I will guide you through hopefully many games of the question-answer-question-answer party game. "
                "Thanks for joining! Now head to the group you want to play the game with and spawn, join and "
                "start a game."))], session)  # TODO UX: return explanation

    @with_session
    def new_game(self, session: Session, chat_id: int, name: str) -> None:
        """
        Create a new game in the given group chat and inform the group about success or cause of failure of this action.

        Make sure that the chat_id actually belongs to a group chat before calling this method.

        :param name: The game's name. May be the group chat name for simplicity
        """
        running_games = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                         model.Game.is_finished == False).count()
        if running_games:
            self._send_messages([Message(chat_id, GetText("Already a running or pending game in this chat"))], session)  # TODO UX: Add hint to COMMAND_STATUS
            return
        game = model.Game(name=name, chat_id=chat_id, is_finished=False, is_started=False, is_waiting_for_finish=False,
                          is_synchronous=True, is_showing_result_names=False)
        logger.info("Created new game %s in chat %s (%s)", game.id, chat_id, name)
        session.add(game)
        self._send_messages([Message(chat_id,
                                     GetText("✅ New game created. Use /{command} to join the game.")  # TODO UX: more info
                                     .format(command=COMMAND_JOIN_GAME))], session)

    @with_session
    def set_rounds(self, session: Session, chat_id: int, rounds: int) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self._send_messages([Message(chat_id, GetText("❌ No game to configure in this chat"))], session)  # TODO UX: Add hint to COMMAND_NEW_GAME
            return
        if game.is_started:
            self._send_messages([Message(chat_id, GetText("❌ Sorry, I can only configure a game before its start. ⏳"))], session)
            return
            # TODO allow rounds change for running games (unless a sheet has > rounds * entries). In this case, sheets with
            #  len(entries) = old_rounds may require to be newly assigned
        if rounds < 1:
            self._send_messages([Message(chat_id, GetText("invalid rounds number. Must be &gt;= 1"))], session)
            return
        logger.info("Setting rounds of game %s to %s", game.id, rounds)
        game.rounds = rounds
        self._send_messages([Message(chat_id, GetText(
            "Number of rounds set: {number_rounds}").format(number_rounds=game.rounds))], session)  # TODO UX

    @with_session
    def set_synchronous(self, session: Session, chat_id: int, state: bool) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self._send_messages([Message(chat_id, GetText("No game to configure in this chat"))], session)  # TODO UX
            return
        if game.is_started:
            self._send_messages([Message(chat_id, GetText(
                "❌ Sorry, I can only configure a game before its start. ⏳"))], session)  # TODO UX
            return
            # TODO allow mode change for running games (requires passing of waiting sheets for sync → unsync)
        logger.info("Setting game %s to %s", game.id, "synchronous" if state else "asynchronous")
        game.is_synchronous = state
        self._send_messages([Message(chat_id, GetText(f"✅ Set game mode."))], session)

    @with_session
    def set_show_result_names(self, session: Session, chat_id: int, state: bool) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self._send_messages([Message(chat_id, GetText("❌ No game to configure in this chat"))], session)  # TODO UX
            return
        if game.is_started:
            self._send_messages([Message(chat_id, GetText(
                "❌ Sorry, I can only configure a game before its start. ⏳"))], session)  # TODO UX
            # TODO should this be possible?
            return
        logger.info("Setting game %s to %s", game.id, "show result names" if state else "not show result names")
        game.is_showing_result_names = state
        self._send_messages([Message(chat_id, GetNoText("✅"))], session)  # TODO UX

    @with_session
    def join_game(self, session: Session, chat_id: int, user_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self._send_messages([Message(chat_id,
                                         GetText("There is currently no pending game in this group. 🙃"
                                                 "Use /{command} to start one.").format(command=COMMAND_NEW_GAME))],
                                session)
            return
        user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        if user is None:
            self._send_messages([Message(chat_id,
                                         GetText("You must start a chat with the bot first. Use the following link: "
                                                 "https://t.me/{bot_name}?{command}=now")
                                         .format(bot_name=self.config['bot']['username'], command=COMMAND_REGISTER))],
                                session)
            return

        new_sheet = False
        if game.is_started:
            # Joining into running games ist only allowed for asynchronous games or in the first round of a synchronous
            # game
            sheet_infos = _game_sheet_infos(game, session)
            if game.is_synchronous:
                if any(si.num_entries == 0 for si in sheet_infos):
                    new_sheet = True
                    logger.info("User %s joins running synchronous game %s in first round", user.id, game.id)
                else:
                    logger.info("User %s cannot join %s, which is already started synchronously.", user.id, game.id)
                    self._send_messages([Message(chat_id, GetText("⏳ Oh no! The game has already started! "
                                                                  "Please join the next game."))], session)
                    return
            else:
                # Add a new sheet if other sheets have only few entries (< ¼ of target rounds), too.
                if min((si.num_entries for si in sheet_infos), default=0) < game.rounds // 4:
                    new_sheet = True
                logger.info("User %s joins running asynchronous game %s %s new sheet", user.id, game.id,
                            "with" if new_sheet else "without")
        else:
            logger.info("User %s joins to game %s", user.id, game.id)
        game.participants.append(model.Participant(user=user))
        messages = [Message(chat_id, GetText("Yay! Welcome {name} 🤗").format(name=user.name))]

        if new_sheet:
            user.pending_sheets.append(model.Sheet(game=game))
            messages.extend(_next_sheet([user], session))
        self._send_messages(messages, session)  # TODO UX

    @with_session
    def start_game(self, session: Session, chat_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self._send_messages([Message(chat_id,
                                         GetText("There is currently no pending game in this Group. "
                                                 "Use /{command} to start one.").format(command=COMMAND_NEW_GAME))],
                                session)
            return
        elif game.is_started:
            self._send_messages([Message(chat_id, GetText("The game is already running"))], session)  # TODO Create status command, refer to it.
            return
        elif len(game.participants) < 2:
            logger.debug("Game %s has not enough participants to be started", game.id)
            self._send_messages([Message(chat_id, GetText(
                "No games with less than two participants permitted 🙅‍♀️"))], session)
            return

        # Create sheets and start game
        for participant in game.participants:
            participant.user.pending_sheets.append(model.Sheet(game=game))
        game.is_started = True

        # Set number of rounds if unset
        if game.rounds is None:
            game.rounds = max(6, math.floor(len(game.participants)/2)*2)
            logger.debug("Setting game %s's rounds automatically to %s", game.id, game.rounds)

        logger.info("Starting game %s", game.id)
        # Give sheets to participants
        result = [Message(chat_id, GetNoText("Let's go!")), Message(chat_id, GetNoText("📝"))]
        result.extend(_next_sheet([p.user for p in game.participants], session))
        self._send_messages(result, session)

    @with_session
    def leave_game(self, session: Session, chat_id: int, user_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self._send_messages([Message(chat_id,
                                         GetText("There is currently no running or pending game in this chat."))],
                                session)
            return
        num_participants = session.query(func.count(model.Participant.user_id))\
            .filter(model.Participant.game == game)\
            .scalar()

        if num_participants <= 2:
            logger.debug("Cannot remove user from game %s, since they are one of 2 or less remaining participants",
                         game.id)
            self._send_messages([Message(chat_id, GetText("You are one of the last two participants of this game. Thus,"
                                                          " you cannot leave."))], session)
            return

        # Remove user as participant from game
        user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        participation = session.query(model.Participant)\
            .filter(model.Participant.user == user, model.Participant.game == game)\
            .one_or_none()
        if participation is None:
            logger.debug("Cannot remove user %s from game %s, since they do not participate. 🤨", user.id, game.id)
            self._send_messages([Message(chat_id, GetText("You didn't participate in this game."))], session)
            return
        session.delete(participation)

        result = [Message(chat_id, GetText("👋 Bye!"))]  # TODO
        logger.info("User %s leaves %sgame %s.", user.id, "running " if game.is_started else "", game.id)

        # Pass on pending sheets
        if user.current_sheet is not None and user.current_sheet.game_id == game.id:
            logger.debug("Retracting sheet %s from user %s, who left the game.", user.current_sheet_id, user.id)
            result.append(Message(user.chat_id, GetText("You left the game. No answer required anymore.")))
            result.extend(_next_sheet([user], session))
        obsolete_sheets = [sheet
                           for sheet in user.pending_sheets
                           if sheet.game_id == game.id]
        logger.debug("Passing sheets %s from user %s, who left the game.",
                     ",".join(str(s.id) for s in obsolete_sheets), user.id)
        _assign_sheet_to_next(obsolete_sheets, game, session)
        result.extend(_next_sheet([sheet.current_user for sheet in obsolete_sheets if sheet.current_user], session))
        self._send_messages(result, session)

    @with_session
    def stop_game(self, session: Session, chat_id: int) -> None:
        """
        Handle a request for a normal game stop in the given group chat.

        This method sets the `Game.is_waiting_for_finish` attribute of the group's current `Game` and checks if the
        stop condition (each sheet ends with an answer) is already satisfied, using
        `_finish_if_stopped_and_all_answered()`. In this case the game is finalized.
        """
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_started == True,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self._send_messages([Message(chat_id, GetText("There is currently no running game in this group."))],
                                session)
            return

        logger.info("Marking game %s to stop at next opportunity. ✋", game.id)
        game.is_waiting_for_finish = True
        sheet_infos = list(_game_sheet_infos(game, session))

        messages = _finish_if_stopped_and_all_answered(game, sheet_infos, session)

        if not game.is_finished:
            logger.info("Retracting answered sheets of game %s to accelerate end of game.", game.id)
            # Retract sheets that do not end with a question (i.e. remove from users' stacks and inform user if it is
            # their current_sheet)
            users_to_update = set()
            for sheet_info in sheet_infos:
                if not sheet_info.num_entries or sheet_info.last_entry.type == model.EntryType.ANSWER:
                    sheet_user: Optional[model.User] = sheet_info.sheet.current_user   # TODO optimize?
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
            messages.extend(_next_sheet(list(users_to_update), session))

        self._send_messages(messages, session)

    @with_session
    def immediately_stop_game(self, session: Session, chat_id: int) -> None:
        """
        Handle a request for an immediate game stop in the given group chat.

        The game is finalized (retracts pending sheets and sends result messages) using `_finalize_game()`.
        """
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_started == True,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self._send_messages([Message(chat_id, GetText("There is currently no running game in this group."))],
                                session)
            return
        logger.info("Immediately stopping game %s.", game.id)
        self._send_messages(_finalize_game(game, session), session)

    @with_session
    def submit_text(self, session: Session, chat_id: int, text: str) -> None:
        """
        Process a message send by a user in their private chat.

        This method may trigger a lot of messages: The user may get request for a submission to the next sheet, another
        user may get the submitted text (or even all participants of the game if a new round of a synchronous game is
        triggered), and/or the game is finalized and result messages are generated.

        :param chat_id: The chat_id in which the message has been received. Should be the private chat with a user.
        :param text: The messages text.
        """
        user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
        if user is None:
            self._send_messages([Message(chat_id, GetText("Unexpected message. Please use /{command} to register with "
                                                          "the bot.").format(command=COMMAND_REGISTER))],
                                session)
            return
        current_sheet = user.current_sheet
        if current_sheet is None:
            logger.debug("Got unexpected text message from user %s (chat_id %s).", user.id, chat_id)
            self._send_messages([Message(chat_id, GetText("Unexpected message."))], session)
            return

        result = []

        # Create new entry
        logger.info("Adding entry by user %s to sheet %s in game %s.",
                    user.id, current_sheet.id, current_sheet.game.id)
        entry_type = (model.EntryType.QUESTION
                      if not current_sheet.entries or current_sheet.entries[-1].type == model.EntryType.ANSWER
                      else model.EntryType.ANSWER)
        current_sheet.entries.append(model.Entry(user=user, text=text, type=entry_type,
                                                 timestamp=datetime.datetime.now(datetime.timezone.utc)))
        result.append(Message(chat_id, GetNoText("🆗")))
        user.current_sheet = None
        current_sheet.current_user = None

        # Check if game is finished
        game = current_sheet.game
        sheet_infos = list(_game_sheet_infos(game, session))
        result.extend(_finish_if_complete(game, sheet_infos, session))
        result.extend(_finish_if_stopped_and_all_answered(game, sheet_infos, session))

        if not game.is_finished and len(current_sheet.entries) < game.rounds:
            # In a synchronous game: Check if the round is finished and pass sheets on
            if game.is_synchronous:
                logger.info("Checking if new round in synchronous game %s should be triggered.", game.id)
                if all(num_entries == len(current_sheet.entries) for sheet, num_entries, last_entry in sheet_infos):
                    logger.info("Triggering new round %s in synchronous game %s.",
                                len(current_sheet.entries) + 1, game.id)
                    # TODO don't assign answered sheets in stopped games? Might be relevant for leaving/joining in-game
                    _assign_sheet_to_next(list(game.sheets), game, session)
                    result.extend(_next_sheet(list(p.user for p in game.participants), session))

            # In an asynchronous game: Pass on this sheet
            elif not game.is_waiting_for_finish or entry_type == model.EntryType.QUESTION:
                _assign_sheet_to_next([current_sheet], game, session)
                assert(current_sheet.current_user is not None)
                result.extend(_next_sheet([current_sheet.current_user], session))

        result.extend(_next_sheet([user], session))
        self._send_messages(result, session)

    # TODO resend_current_sheet()

    @with_session
    def get_user_status(self, session: Session, chat_id: int):
        """
        Send infos about the current state (games, pending sheets) to a user.

        Make sure that the chat_id actually belongs to a private chat before calling this method.
        """
        user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
        if user is None:
            self._send_messages(
                [Message(chat_id, GetText("You are currently not registered for using this bot. Please use "
                                          "/{command} to register with the bot.").format(command=COMMAND_REGISTER))],
                session)
            return

        # Inform about game participations
        running_games = [p.game for p in user.participations if p.game.is_started and not p.game.is_finished]
        pending_games = [p.game for p in user.participations if not p.game.is_started and not p.game.is_finished]

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
        self._send_messages([Message(chat_id, status)] + _next_sheet([user], session, repeat=True), session)

    @with_session
    def get_group_status(self, session: Session, chat_id: int):
        """
        Send infos about the current game state (current running/pending game, players, sheets, entries) to a group.

        Make sure that the chat_id actually belongs to a group chat before calling this method.
        """
        current_game: model.Game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                                    model.Game.is_finished == False).one_or_none()
        if current_game is None:
            status = GetText("There is currently no QAQA-game in this group. Use /{command} to start one.")\
                .format(command=COMMAND_NEW_GAME)
        else:
            players = (GetNoText("* ") + GetNoText('\n* ').join(p.user.name for p in current_game.participants)
                       if current_game.participants
                       else GetText("– none –"))
            configuration = GetText("Rounds: {num_rounds}\nSynchronous: {synchronous}")\
                .format(num_rounds=(GetText('– number of players –')
                                    if current_game.rounds is None
                                    else current_game.rounds),
                        synchronous=GetText('yes') if current_game.is_synchronous else GetText('no'))
            sheet_infos = _game_sheet_infos(current_game, session)
            sheets_stats = (
                GetText(" They have {min}–{max} (Median: {median}) entries yet.")
                .format(min=min(si.num_entries for si in sheet_infos),
                        max=max(si.num_entries for si in sheet_infos),
                        median=statistics.median(si.num_entries for si in sheet_infos))
                if sheet_infos else "")
            pending_sheets = [si.sheet for si in sheet_infos if si.sheet.current_user_id is not None]
            pending_users = (
                GetText("We are currently waiting for {users} 👀\n\n")
                # TODO optimization: access to s.current_user.name with eager loading
                .format(users=','.join(s.current_user.name for s in pending_sheets))
                if current_game.is_synchronous or len(pending_sheets) <= len(sheet_infos) / 3
                else "")
            if current_game.is_started:
                status = GetText("The game is on! 👾\n\n"
                                 "{num_sheets} sheets are in the game.{sheets_stats}\n\n"
                                 "{pending_users}"
                                 "Registered players:\n"
                                 "{players}\n\n"
                                 "Game configuration:\n{configuration}")\
                    .format(num_sheets=len(sheet_infos), sheets_stats=sheets_stats, pending_users=pending_users,
                            players=players, configuration=configuration)
            else:
                status = GetText("The game has been created and waits to be started. 🕰\n"
                                 "Use /{command} to start the game.\n\n"
                                 "Registered players:\n"
                                 "{players}\n\n"
                                 "Game configuration:\n{configuration}")\
                    .format(command=COMMAND_START_GAME, players=players, configuration=configuration)
        self._send_messages([Message(chat_id, status)], session)

    def _send_messages(self, messages: List[Message], session: Session) -> None:
        self._send_callback(_get_translations(messages, session))


def _get_translations(messages: List[Message], session: Session) -> List[TranslatedMessage]:
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


def _game_sheet_infos(game: model.Game, session: Session) -> List[SheetProgressInfo]:
    """ Helper function to get the `SheetProgressInfo` for all sheets of a given Game.

    This list is required by some of the helper functions below. It is generated with an optimized SQL query and may
    be used multiple times, e.g. for checking if a game is finished by `_finish_if_complete` and
    `_finish_if_stopped_and_all_answered`.
    """
    num_subquery = session.query(model.Entry.sheet_id,
                                 func.count('*').label('num_entries')) \
        .group_by(model.Entry.sheet_id) \
        .subquery()
    max_pos_subquery = session.query(model.Entry.sheet_id,
                                     func.max(model.Entry.position).label('max_position')) \
        .group_by(model.Entry.sheet_id) \
        .subquery()
    return [SheetProgressInfo(sheet, num_entries if num_entries is not None else 0, last_entry)
            for sheet, num_entries, last_entry
            in session.query(model.Sheet, num_subquery.c.num_entries, model.Entry)
                .outerjoin(num_subquery, model.Sheet.id == num_subquery.c.sheet_id)
                .outerjoin(max_pos_subquery)
                .outerjoin(model.Entry, and_(model.Entry.sheet_id == model.Sheet.id,
                                             model.Entry.position == max_pos_subquery.c.max_position))
                .filter(model.Sheet.game == game)]


def _next_sheet(users: Iterable[model.User], session: Session, repeat: bool = False) -> List[Message]:
    """ Helper function to check for a list of users, if they have no current sheet, pick the next sheet from their
    pending stack and generate messages to them to ask for their next submission.

    :param users: The users to check for pending sheets and send messages to
    :param repeat: If True, resend the request for submission to all given users, even if they already have a sheet
        assigned."""
    # The following manually crafted SQL query is basically and extended version of _game_sheet_infos() to efficiently
    # query sheets, their entry numbers and last entries along with each User object.
    min_sheet_pos_subquery = session.query(model.Sheet.current_user_id,
                                           func.min(model.Sheet.pending_position).label('min_position')) \
        .group_by(model.Sheet.current_user_id) \
        .subquery()
    num_subquery = session.query(model.Entry.sheet_id,
                                 func.count('*').label('num_entries')) \
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
            result.append(Message(user.chat_id, _format_for_next(
                SheetProgressInfo(next_sheet,
                                  next_sheet_num_entries if next_sheet_num_entries is not None else 0,
                                  next_sheet_last_entry),
                user.current_sheet_id is not None)))
    return result


def _assign_sheet_to_next(sheets: List[model.Sheet], game: model.Game, session: Session):
    """ Assign a list of sheets of a single game to the next user according the game's participant order

    This may be used with a list of all sheets of the game to begin a new round in a synchronous game or a single
    sheet for submissions in an asynchronous game.

    The list of sheets is processed at once for optimization reasons: The order of game participants is generated only
    once and an optimized query is used to fetch all required information about the sheets' entries at once."""
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


def _finish_if_complete(game: model.Game, sheet_infos: Iterable[SheetProgressInfo], session: Session) -> List[Message]:
    """ Finalize the game if it is completed (i.e. all sheets have the number entries).

    This function uses `_finalize_game()` to generate the result messages in this case."""
    logger.debug("Checking game %s for completeness ...", game.id)
    if all(sheet_info.num_entries >= game.rounds for sheet_info in sheet_infos):
        return _finalize_game(game, session)
    return []


def _finish_if_stopped_and_all_answered(game: model.Game, sheet_infos: Iterable[SheetProgressInfo],
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
            return _finalize_game(game, session)
    return []


def _finalize_game(game: model.Game, session: Session) -> List[Message]:
    """ Finalize a game: Collect pending sheets (inform users that no answer is required anymore) and generate result
    messages to the game's group chat."""
    logger.info("Finalizing game %s ...", game.id)
    messages = []
    # Requery sheets with optimized loading of current_user
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
    messages.extend(_next_sheet(list(users_to_update), session))

    # Generate result messages
    for sheet in sheets:
        if sheet.entries:
            messages.extend(Message(game.chat_id, text) for text in _format_result(game, sheet))
    game.is_finished = True
    return messages

def _entry_to_string(game: model.Game, entry: model.Entry) -> str:
    if game.is_showing_result_names:
        return f"\n{entry.user.name}: {entry.text}"
    else:
        return "\n" + entry.text

def _format_result(game: model.Game, sheet: model.Sheet) -> List[LazyGetTextBase]:
    """ Serialize a finished sheet to a list of strings to be sent as result message when finalizing a game. """
    messages = [GetNoText("❓❕  ❔❗️  ⁉️  ‼️")]
    msg = ""
    for entry in sheet.entries:
        # size 4096 is defined by the telegram API as maximal message length
        entry_str = _entry_to_string(game, entry)
        if len(msg) + len(entry_str) < 4096:
            msg += entry_str
        else:
            messages.append(GetNoText(msg))
            msg = entry_str
    messages.append(GetNoText(msg))
    return messages
    # TODO UX: improve


def _format_for_next(sheet_info: SheetProgressInfo, repeat: bool) -> LazyGetTextBase:
    """ Create the message content for showing a sheet to a user and ask them for their next submission. The message
    contains the last entry of the sheet or a request to write the initial question if the sheet is empty.

    :param repeat: True, if this a repeated message for the same sheet and user"""
    if sheet_info.num_entries == 0:
        return GetText("Please ask a question to begin a new sheet for game {game_name}.")\
            .format(game_name=sheet_info.sheet.game.name)
    else:
        assert(sheet_info.last_entry is not None)
        if sheet_info.last_entry.type == model.EntryType.ANSWER:
            return GetText("Please ask a question that may be answered with:\n“{text}”")\
                .format(text=sheet_info.last_entry.text)
        else:
            return GetText("Please answer the following question:\n“{text}”")\
                .format(text=sheet_info.last_entry.text)
