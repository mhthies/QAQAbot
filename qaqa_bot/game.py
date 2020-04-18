"""
This module provides the business logic / game state transformation for the QAQAgamebot. It is encapsulated in
methods of the `GameServer` class, one for each type of user action. In order to be used, a `GameServer` object needs to
be instantiated with a given bot config and a callback method for sending the messages.

Some of the action methods share code fragments (e.g. for finalizing a game under certain conditions), which have been
un-inlined into some helper functions at the bottom of the file.

Additionally, this module defines the `Message` tuple for passing Telegram messages the message sending callback
function and the `@with_session` for magically handling (creating/committing/rolling back) the database sessions.
"""
import functools
from typing import NamedTuple, List, Optional, Iterable, Dict, Any, Callable, MutableMapping

import sqlalchemy
from sqlalchemy import func, and_
from sqlalchemy.orm import Session, joinedload

from . import model

COMMAND_NEW_GAME = "newgame"
COMMAND_JOIN = "join"
COMMAND_REGISTER = "start"


class Message(NamedTuple):
    """ Representation of an outgoing Telegram message, triggered by some game state change """
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
            f(self, session, *args, **kwargs)
            session.commit()
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
                 send_callback: Callable[[List[Message]], None],
                 database_engine=None):
        """
        Initialize a new

        :param config: The bot configuration, read from the `config.toml` file
        :param send_callback: A callback function taking a list of `Message` tuples and sending them via the Telegram
            API. It should raise an exception when sending fails to trigger the database rollback.
        :param database_engine: (optional) Pre-initialized database engine. If not given, a new database engine is
            created, using the `database.connection` entry in the config.
        """
        self.config = config
        self.send_callback = send_callback

        # database_engine may be given (e.g. for testing purposes). Otherwise, we construct one from the configuration
        if database_engine is None:
            database_engine = sqlalchemy.create_engine(config['database']['connection'])

        # Create database session maker
        self.session_maker = sqlalchemy.orm.sessionmaker(bind=database_engine)

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
            self.send_callback([Message(chat_id, "hi again")])  # TODO UX
        else:
            user = model.User(api_id=user_id, chat_id=chat_id, name=user_name)
            session.add(user)
            self.send_callback([Message(chat_id, "hi there")])  # TODO UX: return explanation

    @with_session
    def new_game(self, session: Session, chat_id: int, name: str) -> None:
        running_games = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                         model.Game.is_finished == False).count()
        if running_games:
            self.send_callback([Message(chat_id, "Already a running pending game in this chat")])  # TODO UX: Add hint to COMMAND_NEW_GAME
            return
        game = model.Game(name=name, chat_id=chat_id, is_finished=False, is_started=False, is_synchronous=True)
        session.add(game)
        self.send_callback([Message(chat_id, f"""New game created. Use /{COMMAND_JOIN} to join the game.""")])  # TODO UX: more info

    @with_session
    def set_rounds(self, session: Session, chat_id: int, rounds: int) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, "No game to configure in this chat")])  # TODO UX: Add hint to COMMAND_NEW_GAME
            return
        if game.is_started:
            self.send_callback([Message(chat_id, "Game already started")])
            return
            # TODO allow rounds change for running games (unless a sheet has > rounds * entries). In this case, sheets with
            #  len(entries) = old_rounds may require to be newly assigned
        if rounds < 1:
            self.send_callback([Message(chat_id, "invalid rounds number. Must be >= 1")])
            return
        game.rounds = rounds
        self.send_callback([Message(chat_id, "ok")])  # TODO UX

    @with_session
    def set_synchronous(self, session: Session, chat_id: int, state: bool) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, "No game to configure in this chat")])  # TODO UX
            return
        if game.is_started:
            self.send_callback([Message(chat_id, "Too late")])  # TODO UX
            return
            # TODO allow mode change for running games (requires passing of waiting sheets for sync → unsync)
        game.is_synchronous = state
        self.send_callback([Message(chat_id, "ok")])  # TODO UX

    @with_session
    def join_game(self, session: Session, chat_id: int, user_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, f"There is currently no pending game in this Group. "
                                                 f"Use /{COMMAND_NEW_GAME} to start one.")])
            return
        user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
        if user is None:
            self.send_callback([Message(chat_id, f"You must start a chat with the bot first. Use the following link: "
                                                 f"https://t.me/{self.config['bot']['username']}?start")])
            return
        if game.is_started:
            self.send_callback([Message(chat_id, "Too late")])
            return
            # TODO allow joining into running games
        game.participants.append(model.Participant(user=user))
        self.send_callback([Message(chat_id, "ok")])  # TODO UX

    @with_session
    def start_game(self, session: Session, chat_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, f"There is currently no pending game in this Group. "
                                                 f"Use /{COMMAND_NEW_GAME} to start one.")])
            return
        elif game.is_started:
            self.send_callback([Message(chat_id, "The game is already running")])  # TODO Create status command, refer to it.
            return
        elif len(game.participants) < 2:
            self.send_callback([Message(chat_id, "No games with less than two participants permitted")])
            return

        # Create sheets and start game
        for participant in game.participants:
            participant.user.pending_sheets.append(model.Sheet(game=game))
        game.is_started = True

        # Set number of rounds if unset
        if game.rounds is None:
            game.rounds = len(game.participants)

        # Give sheets to participants
        result = [Message(chat_id, "ok")]
        result.extend(_next_sheet([p.user for p in game.participants], session))
        self.send_callback(result)

    @with_session
    def leave_game(self, session: Session, chat_id: int, user_id: int) -> None:
        game = session.query(model.Game)\
            .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
            .one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, "There is currently no running or pending game in this Chat.")])
            return
        user = session.query(model.User).filter(model.User.api_id == user_id).one()

        # Remove user as participant from game
        participation = session.query(model.Participant).filter(user=user, game=game).one_or_none()
        if participation is None:
            self.send_callback([Message(chat_id, "You didn't participate in this game.")])
            return
        session.delete(participation)

        result = [Message(chat_id, "ok")]  # TODO

        # Pass on pending sheets
        if user.current_sheet is not None and user.current_sheet.game_id == game.id:
            result.append(Message(user.chat_id, "You left the game. No answer required anymore."))
            result.extend(_next_sheet([user], session))
        obsolete_sheets = [sheet
                           for sheet in user.pending_sheets
                           if sheet.game_id == game.id]
        _assign_sheet_to_next(obsolete_sheets, game, session)
        result.extend(_next_sheet([sheet.current_user for sheet in obsolete_sheets], session))
        self.send_callback(result)

    @with_session
    def stop_game(self, session: Session, chat_id: int) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_started == True,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, "There is currently no running game in this Group.")])
            return
        game.is_waiting_for_finish = True
        sheet_infos = list(_game_sheet_infos(game, session))
        self.send_callback(_finish_if_stopped_and_all_answered(game, sheet_infos, session))

    @with_session
    def immediately_stop_game(self, session: Session, chat_id: int) -> None:
        game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                                model.Game.is_started == True,
                                                model.Game.is_finished == False).one_or_none()
        if game is None:
            self.send_callback([Message(chat_id, "There is currently no running game in this Group.")])
            return
        self.send_callback(_finalize_game(game, session))

    @with_session
    def submit_text(self, session: Session, chat_id: int, text: str) -> None:
        user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
        if user is None:
            self.send_callback([Message(chat_id, f"Unexpected message. Please use /{COMMAND_REGISTER} to register with the bot.")])
            return
        current_sheet = user.current_sheet
        if current_sheet is None:
            self.send_callback([Message(chat_id, "Unexpected message.")])
            return

        result = []

        # Create new entry
        entry_type = (model.EntryType.QUESTION
                      if not current_sheet.entries or current_sheet.entries[-1].type == model.EntryType.ANSWER
                      else model.EntryType.ANSWER)
        current_sheet.entries.append(model.Entry(user=user, text=text, type=entry_type))
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
                if all(num_entries == len(current_sheet.entries) for sheet, num_entries, last_entry in sheet_infos):
                    _assign_sheet_to_next(list(game.sheets), game, session)
                    result.extend(_next_sheet(list(p.user for p in game.participants), session))

            # In an asynchronous game: Pass on this sheet
            else:
                _assign_sheet_to_next([current_sheet], game, session)
                assert(current_sheet.current_user is not None)
                result.extend(_next_sheet([current_sheet.current_user], session))

        result.extend(_next_sheet([user], session))
        self.send_callback(result)


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
    """ Get the SheetProgressInfo for all sheets of a given Game """
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


def _next_sheet(users: List[model.User], session: Session) -> List[Message]:
    """ For a list of users, check if they have no current sheet and pick the next sheet from their pending stack."""
    # Extended version of _game_sheet_infos() to efficiently query the users' next sheets, their entry numbers and
    # last entries.
    # TODO exclude answered sheets of games waiting to be stopped
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
                          model.User.current_sheet != None,
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
    for user, has_current_sheet, next_sheet, next_sheet_num_entries, next_sheet_last_entry in query:
        if not has_current_sheet and next_sheet is not None:
            user.current_sheet = next_sheet
            result.append(Message(user.chat_id, _format_for_next(SheetProgressInfo(
                next_sheet,
                next_sheet_num_entries if next_sheet_num_entries is not None else 0,
                next_sheet_last_entry))))
    return result


def _assign_sheet_to_next(sheets: List[model.Sheet], game: model.Game, session: Session):
    """ Assign a list of sheets of a single game to the next user according the game's participant order """
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
        next_mapping[last_entry_by_sheet_id[sheet.id].user_id].pending_sheets.append(sheet)


def _finish_if_complete(game: model.Game, sheet_infos: Iterable[SheetProgressInfo], session: Session) -> List[Message]:
    """ Finish the game if it is completed (i.e. all sheets have enough entries)."""
    if all(sheet_info.num_entries >= game.rounds for sheet_info in sheet_infos):
        return _finalize_game(game, session)
    return []


def _finish_if_stopped_and_all_answered(game: model.Game, sheet_infos: Iterable[SheetProgressInfo],
                                        session: Session) -> List[Message]:
    """ Finish the game if it is waiting for finishing and the finishing condition (each page ends with an answer)."""
    if game.is_waiting_for_finish:
        if all(sheet_info.last_entry is None or sheet_info.last_entry.type == model.EntryType.ANSWER
               for sheet_info in sheet_infos):
            return _finalize_game(game, session)
    return []


def _finalize_game(game: model.Game, session: Session) -> List[Message]:
    """ Finalize a game: Collect pending sheets and send result messages"""
    messages = []
    # Requery sheets with optimized loading of current_user
    sheets = session.query(model.Sheet)\
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
                messages.append(Message(sheet_user.chat_id, "Game was ended. No answer required anymore."))
                users_to_update.add(sheet_user)
    messages.extend(_next_sheet(list(users_to_update), session))

    # Generate result messages
    for sheet in sheets:
        messages.append(Message(game.chat_id, _format_result(sheet)))

    game.is_finished = True
    return messages


def _format_result(sheet: model.Sheet) -> str:
    return "\n".join(entry.text for entry in sheet.entries)  # TODO UX: improve


def _format_for_next(sheet_info: SheetProgressInfo) -> str:
    if sheet_info.num_entries == 0:
        return f"Please ask a question to begin a new sheet for game {sheet_info.sheet.game.name}."
    else:
        assert(sheet_info.last_entry is not None)
        if sheet_info.last_entry.type == model.EntryType.ANSWER:
            return f"Please ask a question that may be answered with:\n“{sheet_info.last_entry.text}”"
        else:
            return f"Please answer the following question:\n“{sheet_info.last_entry.text}”"
