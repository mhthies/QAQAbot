
import unittest
import re
import os.path
from typing import Pattern, Dict, List

import sqlalchemy
import sqlalchemy.orm
import toml

from qaqa_bot import model, game
from qaqa_bot.util import session_scope


CONFIG = toml.load(os.path.join(os.path.dirname(__file__), "test_config.toml"))


class OutgoingMessageStore:
    """ A Mock class to test the message sending of a GameServer """
    def __init__(self):
        self.messages = []

    def send_message(self, messages: List[game.Message]) -> None:
        self.messages.extend(messages)

    def fetch_messages(self) -> List[game.Message]:
        current_messages = self.messages
        self.messages = []
        return current_messages


class FullGameTests(unittest.TestCase):
    def setUp(self) -> None:
        # Setup database schema
        engine = sqlalchemy.create_engine(CONFIG['database']['connection'], echo=True)
        model.Base.metadata.create_all(engine)

        self.message_store = OutgoingMessageStore()
        self.game_server = game.GameServer(CONFIG, self.message_store.send_message, engine)

        # Use
        users = [model.User(api_id=1, chat_id=11, name="Michael"),
                      model.User(api_id=2, chat_id=12, name="Jenny"),
                      model.User(api_id=3, chat_id=13, name="Lukas"),
                      model.User(api_id=4, chat_id=14, name="Jannik")]

        Session = sqlalchemy.orm.sessionmaker(bind=engine)
        with session_scope(Session) as session:
            for user in users:
                session.add(user)

    TEXT_SUBMIT_RESPONSE = r"ok"

    def test_simple_game(self) -> None:
        # Create new game in "Funny Group" chat (chat_id=21)
        self.game_server.new_game(21, "Funny Group")
        # Let Michael, Jenny and Lukas join
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        self.game_server.join_game(21, 3)
        # Set rounds
        self.game_server.set_rounds(21, 2)
        self.message_store.fetch_messages()
        # Start game
        self.game_server.start_game(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("ok"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 13)}})
        # Write questions
        self.game_server.submit_text(11, "Question 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, "Question 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, "Question 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(r"(?s)answer.*?Question 3"),
                                                                         12: re.compile(r"(?s)answer.*?Question 1|"
                                                                                        + self.TEXT_SUBMIT_RESPONSE),
                                                                         13: re.compile(r"(?s)answer.*?Question 2")})
        # Write answers
        self.game_server.submit_text(11, "Answer 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, "Answer 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.get_group_status(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile(r"(?s)game is on.*3 sheets.*waiting for Jenny.*Michael.*"
                                                   r"Synchronous: yes")})
        self.game_server.submit_text(12, "Answer 2")
        self.assertMessagesCorrect(
            self.message_store.fetch_messages(),
            {12: re.compile(self.TEXT_SUBMIT_RESPONSE),
             21: re.compile("(?s)Question 3.*Answer 1|Question 1.*Answer 2|Question 2.*Answer 3")})

    def test_asynchronous_game(self):
        # Create new game in "Funny Group" chat (chat_id=21)
        self.game_server.new_game(21, "Funny Group")
        # Let Michael, Jenny and Lukas join
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        self.game_server.join_game(21, 3)
        # Set settings
        self.game_server.set_rounds(21, 3)
        self.game_server.set_synchronous(21, False)
        self.message_store.fetch_messages()
        # Start game
        self.game_server.start_game(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("ok"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 13)}})
        # Let Michael write question
        self.game_server.submit_text(11, "Question 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, "Question 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {12: re.compile(r"(?s)answer.*?Question 1|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, "Answer 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {12: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, "Question 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {11: re.compile(r"(?s)answer.*?Question 3"),
                                    13: re.compile(r"(?s)answer.*?Question 2|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, "Answer 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {13: re.compile(r"(?s)ask a question.*?Answer 2|" + self.TEXT_SUBMIT_RESPONSE)})

    def test_parallel_games(self):
        # Create new game in "Funny Group" chat (chat_id=21)
        self.game_server.new_game(21, "Funny Group")
        # Let Michael, Jenny join
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        # … and another game in the "Serious Group" chat (chat_id=22)
        self.game_server.new_game(22, "Serious Group")
        # Lukas joins both games
        self.game_server.join_game(22, 3)
        self.game_server.join_game(21, 3)
        # The first Game ist started (in synchronous mode)
        self.game_server.set_rounds(21, 3)
        self.message_store.fetch_messages()
        self.game_server.start_game(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("ok"),
                                    **{i: re.compile("(?s)ask a question.*?Funny Group") for i in (11, 12, 13)}})
        # Michael writes the first question
        self.game_server.submit_text(11, "Question A1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # Jannik and Michael join the second game
        self.game_server.join_game(22, 4)
        self.game_server.join_game(22, 1)
        # The second Game is started (in asynchronous mode)
        self.game_server.set_rounds(22, 3)
        self.game_server.set_synchronous(22, False)
        self.message_store.fetch_messages()
        self.game_server.start_game(22)
        # Only Michael and Jannik should be asked for a question, Lukas is still working on a question for the first
        # game
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {22: re.compile("ok"),
                                    **{i: re.compile("(?s)ask a question.*?Serious Group") for i in (11, 14)}})
        # We now have the following Sheets:
        #   Michael: {G2: }
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G1: }, {G2: }
        #   Jannik: {G2: }

        # Lukas submits two questions
        self.game_server.submit_text(13, "Question A3")
        # The first question waits for the synchronous game …
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {13: re.compile(r"(?s)ask a question.*?Serious Group|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, "Question B3")
        # … the second question is put on Jannik's stack, but he's still working on a question
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # Michael submits one question. He should not get the question in Game 1
        self.game_server.submit_text(11, "Question B1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE),
                                                                         13: re.compile(r"(?s)answer.*?Question B1")})

        # We now have the following Sheets:
        #   Michael: {G1: "Question A3" (waiting)},
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G2: "Question B1"}
        #   Jannik: {G2: }, {G2: "Question B3"},
        # Now, Jenny questions/answers two sheets, the first one triggers a new round in Game 1:
        self.game_server.submit_text(12, "Question A2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(r"(?s)answer.*?Question A3"),
                                                                         12: re.compile(r"(?s)answer.*?Question A1|" +
                                                                                        self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, "Answer A2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {12: re.compile(self.TEXT_SUBMIT_RESPONSE)})

        # We now have the following Sheets:
        #   Michael: {G1: "Question A3"},
        #   Jenny: --
        #   Lukas: {G2: "Question B1"}, {G1: "Question A2"}, {G1: "Question A1", "Answer A2" (waiting)}
        #   Jannik: {G2: }, {G2: "Question B3"},
        self.game_server.get_user_status(13)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {13: re.compile(r"(?s)Serious Group.*Funny Group.*2 pending sheets|Question B1")})
        # Now, let's try to stop Game 2 with all sheets answered
        self.game_server.stop_game(22)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {14: re.compile(r"No new question required|Question B3")})
        self.game_server.submit_text(14, "Answer B4")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {14: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(11, "Answer A1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # We now have the following Sheets:
        #   Michael: {G2: "Question B3", "Answer B4"}
        #   Jenny: {G1: "Question A3", "Answer A1" (waiting)}
        #   Lukas: {G2: "Question B1"}, {G1: "Question A2"}, {G1: "Question A1", "Answer A2" (waiting)},
        #          {G2: "Question B4", "Answer B1"}
        #   Jannik: --
        self.game_server.submit_text(13, "Answer B3")
        self.assertMessagesCorrect(
            self.message_store.fetch_messages(),
            {13: re.compile(r"(?s)answer.*Question A2|" + self.TEXT_SUBMIT_RESPONSE),
             22: re.compile("(?s)Question B3.*Answer B4|Question B1.*Answer B3|Question B4.*Answer B1")})
        self.game_server.submit_text(13, "Answer A3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(r"(?s)ask.*?Answer A3"),
                                                                         12: re.compile(r"(?s)ask.*?Answer A1"),
                                                                         13: re.compile(r"(?s)ask.*?Answer A2|"
                                                                                        + self.TEXT_SUBMIT_RESPONSE)})

    def assertMessagesCorrect(self, messages: List[game.Message], expected: Dict[int, Pattern]) -> None:
        for message in messages:
            self.assertIn(message.chat_id, expected, f"Message \"{message.text}\" to chat id {message.chat_id}")
            self.assertRegex(message.text, expected[message.chat_id])
        for chat in expected:
            self.assertIn(chat, list(m.chat_id for m in messages), f"No message to chat {chat} found")
