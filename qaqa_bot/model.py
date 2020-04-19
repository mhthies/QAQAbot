"""
This module defines the object-relational database model for the QAQA game bot.

A brief overview of the model:

|--------| 1          |-------------| n           |------|
|  Game  | ---------- | Participant | ----------- | User |
|--------|          n |-------------|           1 |------|
  1 |                                               |  | 1 current_user
    |                                               |  |
    | n    1 current_sheet                          |  |
|-------| <-----------------------------------------+  |
| Sheet | ---------------------------------------------+
|-------|  n pending_sheets
  1 |
    | n
|-------|
| Entry |
|-------|

A database schema according to the model can be creating using `Base.metadata.create_all(engine)` with an SQLAlchemy
database engine.
"""

import enum

from sqlalchemy import Column, Integer, BigInteger, String, Boolean, Enum, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.orderinglist import ordering_list


Base = declarative_base()


class Game(Base):
    __tablename__ = 'games'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    chat_id = Column(BigInteger)
    # Game state:
    is_started = Column(Boolean)
    is_waiting_for_finish = Column(Boolean)
    is_finished = Column(Boolean)
    # Game seetings:
    rounds = Column(Integer)
    is_synchronous = Column(Boolean)
    is_showing_result_names = Column(Boolean)

    participants = relationship('Participant', back_populates='game', order_by='Participant.game_order',
                                collection_class=ordering_list('game_order'))
    sheets = relationship('Sheet', back_populates='game')


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    api_id = Column(Integer)
    chat_id = Column(BigInteger)
    name = Column(String)
    # The sheet on which the user is currently working, i.e. which they were requested to add an entry to. Or NULL, if
    # they are not currently working on a sheet. If not NULL, this should always correspond the first entry of
    # `User.pending_sheets`.
    current_sheet_id = Column(Integer, ForeignKey('sheets.id'))

    participations = relationship('Participant', back_populates='user')
    pending_sheets = relationship('Sheet', back_populates='current_user', foreign_keys="Sheet.current_user_id",
                                  order_by='Sheet.pending_position', collection_class=ordering_list('pending_position'))
    current_sheet = relationship('Sheet', foreign_keys=current_sheet_id, post_update=True)


class Participant(Base):
    """
    Relationship between games and users: This n:n relationship is modelled explicitly to store the participants' order
    in each game.
    """
    __tablename__ = 'participants'
    game_id = Column(Integer, ForeignKey('games.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    # Position (sort key) of the participant/user in the game. `Game.participants` is ordered by this key. It is used
    # to determine the order in which the sheets are passed
    game_order = Column(Integer)

    game = relationship('Game', back_populates='participants', lazy='joined')
    user = relationship('User', back_populates='participations', lazy='joined')


class Sheet(Base):
    __tablename__ = 'sheets'
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey('games.id'))
    hint = Column(String)
    # In which user's queue (`User.pending_sheets`) does this sheet wait? May be NULL, if the sheet is finished or the
    # game is synchronous and the sheet is waiting for the next round.
    current_user_id = Column(Integer, ForeignKey('users.id'))
    # Position of this Sheet in the `current_user`'s queue of sheets. Lower sheets are taken first.
    pending_position = Column(Integer)

    game = relationship('Game', back_populates='sheets')
    entries = relationship('Entry', back_populates='sheet', order_by='Entry.position',
                           collection_class=ordering_list('position'))
    current_user = relationship('User', back_populates='pending_sheets', foreign_keys=current_user_id)


class EntryType(enum.Enum):
    QUESTION = 1
    ANSWER = 2


class Entry(Base):
    __tablename__ = 'entries'
    id = Column(Integer, primary_key=True)
    sheet_id = Column(Integer, ForeignKey('sheets.id'))
    # Position (sort key) of the entry on the sheet. `Sheet.entries` is sorted by this.
    position = Column(Integer)
    # The user, who wrote this entry
    user_id = Column(Integer, ForeignKey('users.id'))
    text = Column(String)
    type = Column(Enum(EntryType))

    sheet = relationship('Sheet', back_populates='entries')
    user = relationship('User')
