import itertools
from typing import NamedTuple, List, Optional, TypeVar, Iterable, Tuple
from sqlalchemy.orm import Session
import toml

from . import model

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
        return [Message(chat_id, "No game to configure in this chat")]  # TODO UX
    if game.is_started or game.is_finished:
        raise RuntimeError("Too late")  # TODO No Exception
        # TODO allow mode change for running games (unless a sheet has > rounds * entries). In this case, sheets with
        #  len(entries) = old_rounds may require to be newly assigned
    if rounds < 1:
        raise ValueError("invalid rounds number")  # TODO No Exception
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
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id, model.Game.is_finished == False).one_or_none()
    if game is None:
        return [Message(chat_id, f"There is currently no pending game in this Group. Use /{COMMAND_NEW_GAME} to start one.")]
    user = session.query(model.User).filter(model.User.api_id == user_id).one_or_none()
    if user is None:
        return [Message(chat_id, f"You must start a chat with the bot first. Use the following link: https://t.me/{config['']['botname']}?start")]
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
        return [Message(chat_id, "The game is already running")]  # Create status command, refer to it.
    elif len(game.participants) < 2:
        return [Message(chat_id, "No games with less than two participants permitted")]
    for participant in game.participants:
        participant.user.pending_sheets.append(model.Sheet(game=game))

    if game.rounds is None:
        game.rounds = len(game.participants)

    return [Message(chat_id, "ok")] + list(itertools.chain.from_iterable(_next_sheet(p.user) for p in game.participants))


def leave_game(chat_id: int, user_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id, model.Game.is_finished == False).one_or_none()
    if game is None:
        return [Message(chat_id, "There is currently no running or pending game in this Chat.")]
    user = session.query(model.User).filter(model.User.api_id == user_id).one()
    if game.is_synchronous and game.is_started:
        return [Message(chat_id, "Leaving a running synchronous game is not permitted.")]  # TODO allow?
    # Remove user
    participation = session.query(model.Participant).filter(user=user, game=game).one_or_none()
    if participation is None:
        return [Message(chat_id, "You didn't participate in this game.")]
    session.delete(participation)
    # Pass current sheet to next user
    if user.current_sheet is not None:
        sheet = user.current_sheet
        if sheet.game == game:
            Message(user.chat_id, "You left the game. No answer required anymore.")
    for sheet in list(user.pending_sheets):
        if sheet.game == game:
            sheet.current_user = None
            _assign_sheet_to_next(sheet)
            _next_sheet(sheet.current_user)
            # TODO do something useful for synchronous games
    return [Message(chat_id, "ok")]  # TODO


def stop_game(chat_id: int,session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                            model.Game.is_started == True,
                                            model.Game.is_finished == False).one_or_nont()
    if game is None:
        return [Message(chat_id, "There is currently no running game in this Group.")]
    game.is_waiting_for_finish = True
    return _finish_if_all_answered(game)


def immediately_stop_game(chat_id: int, session: Session) -> List[Message]:
    game = session.query(model.Game).filter(model.Game.chat_id == chat_id,
                                            model.Game.is_started == True,
                                            model.Game.is_finished == False).one_or_nont()
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
    result.extend(_finish_if_all_answered(current_sheet.game))
    result.extend(_finish_if_complete(current_sheet.game))  # TODO optimize to avoid double SELECT and only do as elif-branch

    if len(current_sheet.entries) < current_sheet.game.rounds:
        if current_sheet.game.is_synchronous:
            if all(len(sheet.entries) == len(current_sheet.entries) for sheet in current_sheet.game.sheets):  # TODO optimize with data from above
                for sheet in current_sheet.game.sheets:
                    _assign_sheet_to_next(sheet)  # TODO optimize loop
                for p in current_sheet.game.participants:
                    result.extend(_next_sheet(p.user))  # TODO optimize loop

        else:
            _assign_sheet_to_next(current_sheet)
            result.extend(_next_sheet(current_sheet.current_user))

    result.extend(_next_sheet(user))
    return result


def _next_sheet(user: model.User) -> List[Message]:  # TODO optimize: take first pending sheet and its last entry along with user
    """ Pick the next sheet and query the user for his next entry, if he has no current sheet but sheets on his pending
    stack."""
    if user.current_sheet is None and user.pending_sheets:
        next_sheet = user.pending_sheets[0]
        user.current_sheet = next_sheet
        return [Message(user.chat_id, format_for_next(next_sheet))]  #
    return []


def _assign_sheet_to_next(sheet: model.Sheet):
    """ Assign the given sheet to the next user in the game's participant order """
    next_mapping = dict(pairwise(p.user for p in sheet.game.participants))
    next_mapping[sheet.game.participants[-1].user] = sheet.game.participants[0].user
    sheet.pending_position = None
    next_mapping[sheet.entries[-1].user].pending_sheets.append(sheet)  # TODO optimize: get last entry


T = TypeVar('T')
def pairwise(iterable: Iterable[T]) -> Iterable[Tuple[T, T]]:
    """s -> (s0,s1), (s1,s2), (s2, s3), ...
    From https://docs.python.org/3/library/itertools.html"""
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)


def _finish_if_complete(game: model.Game) -> List[Message]:
    """ Finish the game if it is completed (i.e. all sheets have enough entries)."""
    if all(len(sheet.entries) >= game.rounds for sheet in game.sheets):  # TODO optimize: fetch sheet length from DB
        return _finalize_game(game)
    return []


def _finish_if_all_answered(game: model.Game) -> List[Message]:
    """ Finish the game if it waiting for fininshing and the finishing condition (each page ends with an answer)."""
    if game.is_waiting_for_finish:
        sheets = game.sheets  # TODO optimize: use manual query to get only last entry type. This ↓ is an N+1 query antipattern!
        if all(sheet.entries[-1].type == model.EntryType.ANSWER for sheet in sheets):
            return _finalize_game(game)
    return []


def _finalize_game(game: model.Game) -> List[Message]:
    """ Finalize a game: Collect pending sheets and send result messages """
    messages = []
    # Reset pending sheets
    for sheet in game.sheets:  # TODO optimize: use eager loading for sheet.current_user
        sheet_user: Optional[model.User] = sheet.current_user
        if sheet_user is not None:
            sheet.current_user = None
            if sheet_user.current_sheet == sheet:
                messages.append(Message(sheet_user.chat_id, "Game was ended. No answer required anymore."))
                _next_sheet(sheet_user)

    # Generate result messages
    for sheet in game.sheets:  # eager loading?
        messages.append(Message(game.chat_id, format_result(sheet)))

    game.is_finished = True
    return messages


def format_result(sheet: model.Sheet) -> str:
    return "\n".join(entry.text for entry in sheet.entries)  # TODO UX: improve


def format_for_next(sheet: model.Sheet) -> str:
    if not sheet.entries:
        return f"Please ask a question to begin a new sheet for game {sheet.game.name}."
    else:
        last_entry = sheet.entries[-1]
        if last_entry.type == model.EntryType.ANSWER:
            return f"Please ask a question that may be answered with:\n“{last_entry.text}”"
        else:
            return f"Please answer the following question:\n“{last_entry.text}”"
