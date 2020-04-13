import enum

from sqlalchemy import Table, Column, Integer, BigInteger, String, Boolean, Enum, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.orderinglist import ordering_list


Base = declarative_base()


class Game(Base):
    __tablename__ = 'games'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    chat_id = Column(BigInteger)
    is_started = Column(Boolean)
    is_waiting_for_finish = Column(Boolean)
    is_finished = Column(Boolean)
    rounds = Column(Integer)
    is_synchronous = Column(Boolean)
    is_showing_result_names = Column(Boolean)

    participants = relationship('Participant', back_populates='game', order_by='Participant.game_order',
                                collection_class=ordering_list('game_order'))  # TODO Allow randomized games
    sheets = relationship('Sheet', back_populates='game')


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    api_id = Column(Integer)
    chat_id = Column(BigInteger)
    name = Column(String)
    current_sheet_id = Column(Integer, ForeignKey('sheets.id'))

    participations = relationship('Participant', back_populates='user')
    pending_sheets = relationship('Sheet', back_populates='current_user', foreign_keys="Sheet.current_user_id",
                                  order_by='Sheet.pending_position', collection_class=ordering_list('pending_position'))
    current_sheet = relationship('Sheet', foreign_keys=current_sheet_id, post_update=True)


class Participant(Base):
    __tablename__ = 'participants'
    game_id = Column(Integer, ForeignKey('games.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    game_order = Column(Integer)

    game = relationship('Game', back_populates='participants', lazy='joined')
    user = relationship('User', back_populates='participations', lazy='joined')


class Sheet(Base):
    __tablename__ = 'sheets'
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey('games.id'))
    hint = Column(String)
    current_user_id = Column(Integer, ForeignKey('users.id'))  # On which user's stack does this sheet wait?
    pending_position = Column(Integer)  # sort key for the user's pending sheet stack

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
    position = Column(Integer)
    user_id = Column(Integer, ForeignKey('users.id'))
    text = Column(String)
    type = Column(Enum(EntryType))  # TODO enum types?

    sheet = relationship('Sheet', back_populates='entries')
    user = relationship('User')
