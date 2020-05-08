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

import unittest
import re
from typing import Pattern, Dict, List

import sqlalchemy
import sqlalchemy.orm

from qaqa_bot import model, game
from .util import CONFIG, OutgoingMessageStore, create_sample_users


class FullGameTests(unittest.TestCase):
    def setUp(self) -> None:
        # Setup database schema
        engine = sqlalchemy.create_engine(CONFIG['database']['connection'], echo=True)
        model.Base.metadata.create_all(engine)
        create_sample_users(engine)

        self.message_store = OutgoingMessageStore()
        self.game_server = game.GameServer(CONFIG, self.message_store.send_message, engine)

    TEXT_SUBMIT_RESPONSE = r"🆗"

    def test_leave_game(self) -> None:
        # Create new game in "Funny Group" chat (chat_id=21)
        self.game_server.new_game(21, "Funny Group")
        # Let all users join
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        self.game_server.join_game(21, 3)
        self.game_server.join_game(21, 4)
        # Set rounds
        self.game_server.set_rounds(21, 2)
        self.message_store.fetch_messages()
        # Lukas leaves the game
        self.game_server.leave_game(21, 3)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("👋 Bye!")})
        # Start game
        self.game_server.start_game(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("📝|Let's go!"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 14)}})
        # Jannik leaves the game
        self.game_server.leave_game(21, 4)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("👋 Bye!"),
                                    14: re.compile("No answer required")})
        # Jenny cannot leave the game
        self.game_server.leave_game(21, 4)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("one of the last two participants")})

    def test_simple_game(self) -> None:
        # Create new game in "Funny Group" chat (chat_id=21)
        self.game_server.new_game(21, "Funny Group")
        # Let Michael and Jenny join
        self.game_server.join_game(21, 1)
        self.game_server.join_game(21, 2)
        # Set rounds
        self.game_server.set_rounds(21, 2)
        self.message_store.fetch_messages()
        # Start game
        self.game_server.start_game(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("📝|Let's go!"),
                                    **{i: re.compile("ask a question") for i in (11, 12)}})
        # Write questions
        self.game_server.submit_text(11, 6, "Question 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # Lukas joins late
        self.game_server.join_game(21, 3)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("Welcome Lukas"),
                                    13: re.compile("ask a question")})
        self.game_server.submit_text(13, 7, "Quetsion 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.edit_submitted_message(13, 7, "Quetion 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, 8, "Question 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(r"(?s)answer.*?Quetion 3"),
                                                                         12: re.compile(r"(?s)answer.*?Question 1|"
                                                                                        + self.TEXT_SUBMIT_RESPONSE),
                                                                         13: re.compile(r"(?s)answer.*?Question 2")})
        self.game_server.edit_submitted_message(13, 7, "Question 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {11: re.compile(r"Question 3|updated"),
                                    13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # Jannik wants to join too, but it's too late
        self.game_server.join_game(21, 4)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile("already started")})
        # Write answers
        self.game_server.submit_text(11, 9, "Answer 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.edit_submitted_message(13, 7, "Question Q3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(r"not accepted")})
        self.game_server.submit_text(13, 10, "Answer 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.get_group_status(21)
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {21: re.compile(r"(?s)game is on.*3 sheets.*waiting for Jenny.*Michael.*"
                                                   r"Synchronous: yes")})
        self.game_server.submit_text(12, 11, "Answer 2")
        self.assertMessagesCorrect(
            self.message_store.fetch_messages(),
            {12: re.compile(self.TEXT_SUBMIT_RESPONSE),
             21: re.compile("example.com:9090/game/")})
        self.game_server.edit_submitted_message(13, 10, "Answer A3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(r"not accepted")})

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
                                   {21: re.compile("📝|Let's go!"),
                                    **{i: re.compile("ask a question") for i in (11, 12, 13)}})
        # Let Michael write question
        self.game_server.submit_text(11, 1, "Question 1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, 2, "Question 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {12: re.compile(r"(?s)answer.*?Question 1|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, 3, "Answer 2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {12: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, 4, "Question 3")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {11: re.compile(r"(?s)answer.*?Question 3"),
                                    13: re.compile(r"(?s)answer.*?Question 2|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, 5, "Answer 3")
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
                                   {21: re.compile("📝|Let's go!"),
                                    **{i: re.compile("(?s)ask a question.*?Funny Group") for i in (11, 12, 13)}})
        # Michael writes the first question
        self.game_server.submit_text(11, 12, "Question A1")
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
                                   {22: re.compile("📝|Let's go!"),
                                    **{i: re.compile("(?s)ask a question.*?Serious Group") for i in (11, 14)}})
        # We now have the following Sheets:
        #   Michael: {G2: }
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G1: }, {G2: }
        #   Jannik: {G2: }

        # Lukas submits two questions
        self.game_server.submit_text(13, 13, "Question A3")
        # The first question waits for the synchronous game …
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {13: re.compile(r"(?s)ask a question.*?Serious Group|" + self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(13, 14, "Question B3")
        # … the second question is put on Jannik's stack, but he's still working on a question
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {13: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # Michael submits one question. He should not get the question in Game 1
        self.game_server.submit_text(11, 15, "Question B1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(self.TEXT_SUBMIT_RESPONSE),
                                                                         13: re.compile(r"(?s)answer.*?Question B1")})

        # We now have the following Sheets:
        #   Michael: {G1: "Question A3" (waiting)},
        #   Jenny: {G1: }, {G1: "Question A1" (waiting)}
        #   Lukas: {G2: "Question B1"}
        #   Jannik: {G2: }, {G2: "Question B3"},
        # Now, Jenny questions/answers two sheets, the first one triggers a new round in Game 1:
        self.game_server.submit_text(12, 20, "Question A2")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {11: re.compile(r"(?s)answer.*?Question A3"),
                                                                         12: re.compile(r"(?s)answer.*?Question A1|" +
                                                                                        self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(12, 21, "Answer A2")
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
        self.game_server.submit_text(14, 16, "Answer B4")
        self.assertMessagesCorrect(self.message_store.fetch_messages(), {14: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        self.game_server.submit_text(11, 17, "Answer A1")
        self.assertMessagesCorrect(self.message_store.fetch_messages(),
                                   {11: re.compile(self.TEXT_SUBMIT_RESPONSE)})
        # We now have the following Sheets:
        #   Michael: {G2: "Question B3", "Answer B4"}
        #   Jenny: {G1: "Question A3", "Answer A1" (waiting)}
        #   Lukas: {G2: "Question B1"}, {G1: "Question A2"}, {G1: "Question A1", "Answer A2" (waiting)},
        #          {G2: "Question B4", "Answer B1"}
        #   Jannik: --
        self.game_server.submit_text(13, 18, "Answer B3")
        self.assertMessagesCorrect(
            self.message_store.fetch_messages(),
            {13: re.compile(r"(?s)answer.*Question A2|" + self.TEXT_SUBMIT_RESPONSE),
             22: re.compile("example.com:9090/game/")})
        self.game_server.submit_text(13, 19, "Answer A3")
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
