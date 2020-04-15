
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
        # Let Michael, Jenny and Lukas join
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
        # Let Michael, Jenny and Lukas join
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

    def test_parallel_games(self):
        self._setup_users()
        # Create new game in "Funny Group" chat (chat_id=21)
        with session_scope(self.Session) as session:
            game.new_game(21, "Funny Group", session)
        # Let Michael, Jenny join
        with session_scope(self.Session) as session:
            game.join_game(21, 1, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 2, session)
        # … and another game in the "Serious Group" chat (chat_id=22)
        with session_scope(self.Session) as session:
            game.new_game(22, "Serious Group", session)
        # Lukas joins both games
        with session_scope(self.Session) as session:
            game.join_game(22, 3, session)
        with session_scope(self.Session) as session:
            game.join_game(21, 3, session)
        # The first Game ist started (in synchronous mode)
        with session_scope(self.Session) as session:
            game.set_rounds(21, 3, session)
        with session_scope(self.Session) as session:
            msg = game.start_game(21, session)
        self.assertMessagesCorrect(msg,
                                   {21: re.compile("ok"),
                                    **{i: re.compile("(?s)ask a question.*?Funny Group") for i in (11, 12, 13)}})
        # Michael writes the first question
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Question A1", session)
        self.assertMessagesCorrect(msg, {})
        # Jannik and Michael join the second game
        with session_scope(self.Session) as session:
            game.join_game(22, 4, session)
        with session_scope(self.Session) as session:
            game.join_game(22, 1, session)
        # The second Game is started (in asynchronous mode)
        with session_scope(self.Session) as session:
            game.set_rounds(22, 3, session)
        with session_scope(self.Session) as session:
            game.set_synchronous(22, False, session)
        with session_scope(self.Session) as session:
            msg = game.start_game(22, session)
        # Only Michael and Jannik should be asked for a question, Lukas is still working on a question for the first
        # game
        self.assertMessagesCorrect(msg,
                                   {22: re.compile("ok"),
                                    **{i: re.compile("(?s)ask a question.*?Serious Group") for i in (11, 14)}})
        # We now have the following Sheets:
        #   Michael: {G2: }
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G1: }, {G2: }
        #   Jannik: {G2: }

        # Lukas submits two questions
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Question A3", session)
        # The first question waits for the synchronous game …
        self.assertMessagesCorrect(msg, {13: re.compile(r"(?s)ask a question.*?Serious Group")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Question B3", session)
        # … the second question is put on Jannik's stack, but he's still working on a question
        self.assertMessagesCorrect(msg, {})
        # Michael submits one question. He should not get the question in Game 1
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Question B1", session)
        self.assertMessagesCorrect(msg, {13: re.compile(r"(?s)answer.*?Question B1")})

        # We now have the following Sheets:
        #   Michael: {G1: "Question A3" (waiting)},
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G2: "Question B1"}
        #   Jannik: {G2: }, {G2: "Question B3"},
        # Now, Jenny questions/answers two sheets, the first one triggers a new round in Game 1:
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Question A2", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)answer.*?Question A3"),
                                         12: re.compile(r"(?s)answer.*?Question A1")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(12, "Answer A2", session)
        self.assertMessagesCorrect(msg, {})

        # We now have the following Sheets:
        #   Michael: {G1: "Question A3"},
        #   Jenny: --
        #   Lukas: {G2: "Question B1"}, {G1: "Question A2"}, {G1: "Question A1", "Answer A2" (waiting)}
        #   Jannik: {G2: }, {G2: "Question B3"},
        # Now, let's try to stop Game 2 with all sheets answered
        with session_scope(self.Session) as session:
            msg = game.stop_game(22, session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(14, "Question B4", session)
        self.assertMessagesCorrect(msg, {14: re.compile(r"(?s)answer.*?Question B3")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(14, "Answer B4", session)
        self.assertMessagesCorrect(msg, {})
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Answer A1", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)answer.*?Question B4")})
        with session_scope(self.Session) as session:
            msg = game.submit_text(11, "Answer B1", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)ask a question.*?Answer B4")})
        # We now have the following Sheets:
        #   Michael: {G2: "Question B3", "Answer B4"}
        #   Jenny: {G1: "Question A3", "Answer A1" (waiting)}
        #   Lukas: {G2: "Question B1"}, {G1: "Question A2"}, {G1: "Question A1", "Answer A2" (waiting)},
        #          {G2: "Question B4", "Answer B1"}
        #   Jannik: --
        with session_scope(self.Session) as session:
            msg = game.submit_text(13, "Answer B3", session)
        self.assertMessagesCorrect(msg, {11: re.compile(r"(?s)No answer required"),
                                         13: re.compile(r"(?s)No answer required|answer.*Question A2"),
                                         22: re.compile("(?s)Question B3.*Answer B4|Question B1.*Answer B3|Question B4.*Answer B1")})

    def assertMessagesCorrect(self, messages: List[game.Message], expected: Dict[int, Pattern]) -> None:
        for message in messages:
            self.assertIn(message.chat_id, expected, f"Message \"{message.text}\" to chat id {message.chat_id}")
            self.assertRegex(message.text, expected[message.chat_id])
        for chat in expected:
            self.assertIn(chat, list(m.chat_id for m in messages), f"No message to chat {chat} found")
