from typing import NamedTuple, List, Optional, Tuple, Iterable

from sqlalchemy import func, and_
from sqlalchemy.orm import Session, joinedload, aliased
import toml

from . import model, util

COMMAND_NEW_GAME = "newgame"
COMMAND_JOIN = "join"
COMMAND_REGISTER = "start"

config = toml.load("config.toml")  # TODO


class Message(NamedTuple):
    chat_id: int
    text: str


def register_user(chat_id: int, user_id: int, user_name: str, session: Session) -> List[Message]:
    existing_user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
    if existing_user is not None:
        return [Message(chat_id, "already registered")]
    user = model.User(api_id=user_id, chat_id=chat_id, name=user_name)
    session.add(user)
    return [Message(chat_id, "hi there")]  # TODO UX: return explanation


def give_help_meesage(chat_id: int) -> List[Message]:
    return [Message(chat_id, "help message not implemented")]  # TODO UX


def new_game(chat_id: int, name: str, session: Session) -> List[Message]:
    # TODO prevent new games in single user chats
    game = model.Game(name=name, chat_id=chat_id, is_finished=False, is_started=False, is_synchronous=True)
    session.add(game)
    result = Message(chat_id, f"""New game created. Use /{COMMAND_JOIN} to join the game.""")  # TODO UX: more info
    return [result]


def set_rounds(chat_id: int, rounds: int, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id).one_or_none()
    if game is None:
        return [Message(chat_id, "No game to configure in this chat")]  # TODO UX: Add hint to COMMAND_NEW_GAME
    if game.is_started or game.is_finished:
        return [Message(chat_id, "Game already started")]
        # TODO allow rounds change for running games (unless a sheet has > rounds * entries). In this case, sheets with
        #  len(entries) = old_rounds may require to be newly assigned
    if rounds < 1:
        return [Message(chat_id, "invalid rounds number. Must be >= 1")]
    game.rounds = rounds
    return [Message(chat_id, "ok")]  # TODO UX


def set_synchronous(chat_id: int, state: bool, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id).one_or_none()
    if game is None:
        return [Message(chat_id, "No game to configure in this chat")]  # TODO UX
    if game.chat_id != chat_id:
        raise RuntimeError("Wrong chat")  # TODO No Exception
    if game.is_started or game.is_finished:
        raise RuntimeError("Too late")  # TODO No Exception
        # TODO allow mode change for running games (requires passing of waiting sheets for sync → unsync)
    game.is_synchronous = state
    return [Message(chat_id, "ok")]  # TODO UX


def join_game(chat_id: int, user_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game)\
        .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
        .one_or_none()
    if game is None:
        return [Message(chat_id, f"There is currently no pending game in this Group. "
                                 f"Use /{COMMAND_NEW_GAME} to start one.")]
    user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
    if user is None:
        return [Message(chat_id, f"You must start a chat with the bot first. Use the following link: "
                                 f"https://t.me/{config['']['botname']}?start")]
    if game.is_started:
        raise RuntimeError("Too late")  # TODO No Exception
        # TODO allow joining into running games
    game.participants.append(model.Participant(user=user))
    return [Message(chat_id, "ok")]  # TODO UX


def start_game(chat_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game)\
        .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
        .one_or_none()
    if game is None:
        return [Message(chat_id, f"There is currently no pending game in this Group. "
                                 f"Use /{COMMAND_NEW_GAME} to start one.")]
    elif game.is_started:
        return [Message(chat_id, "The game is already running")]  # TODO Create status command, refer to it.
    elif len(game.participants) < 2:
        return [Message(chat_id, "No games with less than two participants permitted")]

    # Create sheets
    for participant in game.participants:
        participant.user.pending_sheets.append(model.Sheet(game=game))

    # Set number of rounds if unset
    if game.rounds is None:
        game.rounds = len(game.participants)

    # Give sheets to participants
    result = [Message(chat_id, "ok")]
    result.extend(_next_sheet([p.user for p in game.participants], session))

    return result


def leave_game(chat_id: int, user_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game)\
        .filter(model.Game.chat_id == chat_id, model.Game.is_finished == False)\
        .one_or_none()
    if game is None:
        return [Message(chat_id, "There is currently no running or pending game in this Chat.")]
    user = session.query(model.User).filter(model.User.api_id == user_id).one()
    if game.is_synchronous and game.is_started:
        return [Message(chat_id, "Leaving a running synchronous game is not permitted.")]  # TODO allow?

    # Remove user as participant from game
    participation = session.query(model.Participant).filter(user=user, game=game).one_or_none()
    if participation is None:
        return [Message(chat_id, "You didn't participate in this game.")]
    session.delete(participation)

    result = [Message(chat_id, "ok")]  # TODO

    # Pass on pending sheets
    if user.current_sheet is not None and user.current_sheet.game_id == game.id:
        result.append(Message(user.chat_id, "You left the game. No answer required anymore."))
        result.extend(_next_sheet([user], session))
    for sheet in list(user.pending_sheets):
        if sheet.game_id == game.id:
            sheet.current_user = None
            _assign_sheet_to_next(sheet)
            result.extend(_next_sheet([sheet.current_user], session))  # TODO optimize
    return result


def stop_game(chat_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                            model.Game.is_started == True,
                                            model.Game.is_finished == False).one_or_nont()
    if game is None:
        return [Message(chat_id, "There is currently no running game in this Group.")]
    game.is_waiting_for_finish = True
    return _finish_if_stopped_and_all_answered(game, session)


def immediately_stop_game(chat_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                            model.Game.is_started == True,
                                            model.Game.is_finished == False).one_or_none()
    if game is None:
        return [Message(chat_id, "There is currently no running game in this Group.")]
    return _finalize_game(game)


def submit_text(chat_id: int, text: str, session: Session) -> List[Message]:
    user = session.query(model.User).filter(model.User.chat_id == chat_id).one_or_none()
    if user is None:
        return [Message(chat_id, f"Unexpected message. Please use /{COMMAND_REGISTER} to register with the bot.")]
    current_sheet = user.current_sheet
    if current_sheet is None:
        return [Message(chat_id, "Unexpected message.")]

    result = []

    # Create new entry
    entry_type = (model.EntryType.QUESTION
                  if not current_sheet.entries or current_sheet.entries[-1].type == model.EntryType.ANSWER
                  else model.EntryType.ANSWER)
    current_sheet.entries.append(model.Entry(user=user, text=text, type=entry_type))
    user.current_sheet = None
    current_sheet.current_user = None

    # Check if game is finished
    sheet_infos = list(_game_sheet_infos(current_sheet.game, session))
    result.extend(_finish_if_complete(current_sheet.game, sheet_infos, session))
    result.extend(_finish_if_stopped_and_all_answered(current_sheet.game, sheet_infos, session))

    if len(current_sheet.entries) < current_sheet.game.rounds:
        if current_sheet.game.is_synchronous:
            if all(len(sheet.entries) == len(current_sheet.entries) for sheet in current_sheet.game.sheets):  # TODO optimize with data from above
                for sheet in current_sheet.game.sheets:
                    _assign_sheet_to_next(sheet)  # TODO optimize loop
                result.extend(_next_sheet(list(p.user for p in current_sheet.game.participants), session))

        else:
            _assign_sheet_to_next(current_sheet)
            result.extend(_next_sheet([current_sheet.current_user], session))

    result.extend(_next_sheet([user], session))
    return result


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


def _assign_sheet_to_next(sheet: model.Sheet):
    """ Assign the given sheet to the next user in the game's participant order """
    next_mapping = dict(util.pairwise(p.user for p in sheet.game.participants))
    next_mapping[sheet.game.participants[-1].user] = sheet.game.participants[0].user
    sheet.pending_position = None
    next_mapping[sheet.entries[-1].user].pending_sheets.append(sheet)  # TODO optimize: get last entry


def _finish_if_complete(game: model.Game, sheet_infos: Iterable[SheetProgressInfo], session: Session) -> List[Message]:
    """ Finish the game if it is completed (i.e. all sheets have enough entries)."""
    if all(sheet_info.num_entries >= game.rounds for sheet_info in sheet_infos):
        return _finalize_game(game, session)
    return []


def _finish_if_stopped_and_all_answered(game: model.Game, sheet_infos: Iterable[SheetProgressInfo], session: Session) -> List[Message]:
    """ Finish the game if it is waiting for finishing and the finishing condition (each page ends with an answer)."""
    if game.is_waiting_for_finish:
        if all(sheet_info.last_entry.type == model.EntryType.ANSWER for sheet_info in sheet_infos):
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
        if sheet_info.last_entry.type == model.EntryType.ANSWER:
            return f"Please ask a question that may be answered with:\n“{sheet_info.last_entry.text}”"
        else:
            return f"Please answer the following question:\n“{sheet_info.last_entry.text}”"
