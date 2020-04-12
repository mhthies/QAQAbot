
import unittest
import re
from typing import Pattern, Dict, List

import sqlalchemy
import sqlalchemy.orm

from qaqa_bot import model, game
from qaqa_bot.util import session_scope


class FullGameTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = sqlalchemy.create_engine('sqlite:///:memory:', echo=True)
        model.Base.metadata.create_all(engine)
        self.Session = sqlalchemy.orm.sessionmaker(bind=engine)

    def _setup_users(self) -> None:
        users = [model.User(api_id=1, chat_id=11, name="Michael"),
                      model.User(api_id=2, chat_id=12, name="Jenny"),
                      model.User(api_id=3, chat_id=13, name="Lukas"),
                      model.User(api_id=4, chat_id=14, name="Jannik")]
        with session_scope(self.Session) as session:
            for user in users:
                session.add(user)

    def test_simple_game(self) -> None:
        self._setup_users()
        # Create new game in "Funny Group" chat (chat_id=21)
        with session_scope(self.Session) as session:
            game.new_game(21, "Funny Group", session)
        # Let Michael, Jenny and Jannik join
        with session_scope(self.Session) as session:
            game.join_game(21, 1, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 2, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 3, session)
        # Set rounds
        with session_scope(self.Session) as session:
            game.set_rounds(21, 2, session)
        # Start game
        with session_scope(self.Session) as session:
            msg = game.start_game(21, session)
        self.assertMessagesCorrect(msg,
                                   {21: re.compile("ok"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 13)}})
        # Write questions
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Question 1", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Question 3", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Question 2", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)answer.*?Question 3"),
                                         12: re.compile(r"(?s)answer.*?Question 1"),
                                         13: re.compile(r"(?s)answer.*?Question 2")})
        # Write answers
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Answer 1", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Answer 3", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Answer 2", session)
        self.assertMessagesCorrect(
            msg, {21: re.compile("(?s)Question 3.*Answer 1|Question 1.*Answer 2|Question 2.*Answer 3")})

    def test_asynchronous_game(self):
        self._setup_users()
        # Create new game in "Funny Group" chat (chat_id=21)
        with session_scope(self.Session) as session:
            game.new_game(21, "Funny Group", session)
        # Let Michael, Jenny and Jannik join
        with session_scope(self.Session) as session:
            game.join_game(21, 1, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 2, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 3, session)
        # Set settings
        with session_scope(self.Session) as session:
            game.set_rounds(21, 3, session)
        with session_scope(self.Session) as session:
            game.set_synchronous(21, False, session)
        # Start game
        with session_scope(self.Session) as session:
            msg = game.start_game(21, session)
        self.assertMessagesCorrect(msg,
                                   {21: re.compile("ok"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 13)}})
        # Let Michael write question
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Question 1", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Question 2", session)
        self.assertMessagesCorrect(msg, {12: re.compile(r"(?s)answer.*?Question 1")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Answer 2", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Question 3", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)answer.*?Question 3"),
                                         13: re.compile(r"(?s)answer.*?Question 2")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Answer 3", session)
        self.assertMessagesCorrect(msg, {13: re.compile(r"(?s)ask a question.*?Answer 2")})
        # TODO test stopping game

    def assertMessagesCorrect(self, messages: List[game.Message], expected: Dict[int, Pattern]) -> None:
        for message in messages:
            self.assertIn(message.chat_id, expected, f"Message \"{message.text}\" to chat id {message.chat_id}")
            self.assertRegex(message.text, expected[message.chat_id])
        for chat in expected:
            self.assertIn(chat, list(m.chat_id for m in messages), f"No message to chat {chat} found")
