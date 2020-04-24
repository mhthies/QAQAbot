# Copyright 2020 Jennifer Krieger
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

import logging
from typing import List
import re

import telegram
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler)

from . import game
from .util import GetText

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

LANGUAGES = {'de': '🇩🇪',
             'en': '🇬🇧'}
BOOL = {"True": "Yes 👍", "False": "No 👎"}
SYNC = {"True": "Synchronous mode", "False": "Asynchronous mode"}

class Frontend:
    def __init__(self, config):
        self.config = config
        token: str = config["bot"]["api_key"]

        # Updater and dispatcher
        self.updater = Updater(token=token, use_context=True)
        self.dispatcher = self.updater.dispatcher

        # Command general activities
        help_handler = CommandHandler(game.COMMAND_HELP, self.help, filters=~Filters.update.edited_message)
        status_handler = CommandHandler(game.COMMAND_STATUS, self.status, filters=~Filters.update.edited_message)
        set_language_handler = CommandHandler(game.COMMAND_SET_LANGUAGE, self.set_language)
        self.dispatcher.add_handler(help_handler)
        self.dispatcher.add_handler(status_handler)
        self.dispatcher.add_handler(set_language_handler)

        self.dispatcher.add_handler(CallbackQueryHandler(self.button))

        # Commandhandler private activities
        start_handler = CommandHandler(game.COMMAND_REGISTER, self.start, filters=~Filters.update.edited_message)
        self.dispatcher.add_handler(start_handler)
        # Commandhandler group activites
        start_game_handler = CommandHandler(game.COMMAND_START_GAME, self.start_game,
                                            filters=~Filters.update.edited_message)
        new_game_handler = CommandHandler(game.COMMAND_NEW_GAME, self.new_game, filters=~Filters.update.edited_message)
        join_game_handler = CommandHandler(game.COMMAND_JOIN_GAME, self.join_game,
                                           filters=~Filters.update.edited_message)
        stop_game_handler = CommandHandler(game.COMMAND_STOP_GAME, self.stop_game,
                                           filters=~Filters.update.edited_message)
        stop_game_immediately_handler = CommandHandler(game.COMMAND_STOP_GAME_IMMEDIATELY, self.stop_game_immediately,
                                                       filters=~Filters.update.edited_message)
        set_rounds_handler = CommandHandler(game.COMMAND_SET_ROUNDS, self.set_rounds, pass_args=True,
                                            filters=~Filters.update.edited_message)
        set_synchronous_handler = CommandHandler(game.COMMAND_SET_SYNC, self.set_sync,
                                                 filters=~Filters.update.edited_message)
        set_display_name_handler = CommandHandler(game.COMMAND_SET_DISPLAY_NAME, self.set_display_name,
                                                  filters=~Filters.update.edited_message)
        self.dispatcher.add_handler(start_game_handler)
        self.dispatcher.add_handler(new_game_handler)
        self.dispatcher.add_handler(join_game_handler)
        self.dispatcher.add_handler(stop_game_handler)
        self.dispatcher.add_handler(stop_game_immediately_handler)
        self.dispatcher.add_handler(set_rounds_handler)
        self.dispatcher.add_handler(set_synchronous_handler)
        self.dispatcher.add_handler(set_display_name_handler)

        # Messagehandler
        message_handler = MessageHandler(filters=telegram.ext.filters.MergedFilter(
            Filters.text, ~Filters.update.edited_message), callback=self.incoming_message)
        self.dispatcher.add_handler(message_handler)
        message_edit_handler = MessageHandler(filters=telegram.ext.filters.MergedFilter(
            Filters.update.edited_message, Filters.command), callback=self.edited_message)
        self.dispatcher.add_handler(message_edit_handler)

        # Errorhandling
        self.dispatcher.add_error_handler(self.error)

        # Gameserver
        self.gs = game.GameServer(config=config, send_callback=self.send_messages)

    def set_commands(self):
        """Sends the commands to the BotFather."""
        actual: List[(str,str)] = (self.updater.bot.getMyCommands())
        print(actual[0].command)
        commands: List[BotCommand] = [
            BotCommand(game.COMMAND_HELP, "Explains the commands."),
            BotCommand(game.COMMAND_REGISTER, "Let the bot talk to you. Necessary for playing the game."),
            BotCommand(game.COMMAND_NEW_GAME,
                       "Spawns a game.Default settings: asynchronous, number of rounds is the number of players."),
            BotCommand(game.COMMAND_JOIN_GAME, "Join the current game."),
            BotCommand(game.COMMAND_START_GAME, "Start the game.This does only work if a game has been spawned."),
            BotCommand(game.COMMAND_SET_ROUNDS, "Defines the number of rounds for the actual game."),
            BotCommand(game.COMMAND_SET_SYNC, "Choose between synchronous mode (pass all sheets at once and "
                                              "asynchronous mode (pass sheet ASAP)."),
            BotCommand(game.COMMAND_SET_DISPLAY_NAME, "Define whether to show the authors names in the result."),
            BotCommand(game.COMMAND_SET_LANGUAGE, f"Choose the language. Available atm: "
                                                  f"{', '.join(flag for code, flag in LANGUAGES.items())}."),
            BotCommand(game.COMMAND_STOP_GAME, "Stops the game after the current round."),
            BotCommand(game.COMMAND_STOP_GAME_IMMEDIATELY,
                       "Stops the game without waiting for the round to be finished.")]
        self.updater.bot.set_my_commands(commands)

    def start_bot(self):
        """Starts polling for user interaction.

        This method is blocking: It blocks the calling thread until an interrupt signal is received, using
        `Updater.idle()`.
        """
        self.updater.start_polling()
        self.updater.idle()

    def start(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Send a friendly welcome message with language set to locale."""
        chat_id: int = update.effective_chat.id
        self.gs.set_chat_locale(chat_id, update.message.from_user.language_code)
        if update.message.chat.type == "private":
            name = (f"@{update.message.from_user.username}"
                    if update.message.from_user.username is not None
                    else update.message.from_user.first_name)
            self.gs.register_user(update.message.chat.id, update.message.from_user.id, name)
            logger.debug(msg=f"{update.message.from_user.first_name} registered.")
        else:
            logger.debug(msg=f"Chat {chat_id} sends start-commands")

    def new_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.debug(msg=f"Try to spawn a game in {chat_id}")
            self.gs.new_game(chat_id=chat_id, name=update.message.chat.title)
        else:
            self.gs.send_messages([game.Message(chat_id, GetText("Games can only be spawned in group chats."))])

    def start_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Start game in current chat."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.debug(msg=f"Try to start a game in {chat_id}")
            self.gs.start_game(chat_id)
        else:
            self.gs.send_messages([game.Message(chat_id, GetText("Games can only be started in group chats."))])

    def join_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext):
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.info(msg=f"{update.message.from_user} tries to join a game in {chat_id}")
            self.gs.join_game(chat_id=chat_id, user_id=update.message.from_user.id)
        else:
            self.gs.send_messages([game.Message(chat_id, GetText("Games can only be joined in group chats."))])

    def incoming_message(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Parse a text-message that was send to the bot in a private chat."""
        text = update.message.text
        logger.info(msg=f"Got message from {update.message.from_user.first_name}: {text}")
        logger.info(update)
        if re.search(r'\/.*', text):
            self.gs.send_messages([game.Message(update.message.chat.id,
                                                GetText("Sorry, this is not a valid command. 🧐"))])
        else:
            submitted_text = update.message.text_html_urled.replace('\n', ' ‖ ')
            self.gs.submit_text(update.message.chat.id, submitted_text)

    def edited_message(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        logger.info(msg=f"Message edited!")

    def stop_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Stop the game after the current round."""
        self.gs.stop_game(chat_id=update.message.chat.id)

    def stop_game_immediately(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Stop the game without awaiting the end of the current round."""
        self.gs.immediately_stop_game(chat_id=update.message.chat.id)

    def set_rounds(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Set the number of rounds"""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "private":
            self.gs.send_messages([game.Message(chat_id, GetText("Games can only edited in group chats."))])
            return
        # Accept just one parameter and when given more or less
        if len(context.args) == 1:
            try:
                rounds: int = int(context.args[0])
                self.gs.set_rounds(chat_id, rounds)
            except ValueError:
                self.gs.send_messages([game.Message(chat_id, GetText("‘{arg}’ is not a number of rounds!").
                                                 format(arg=context.args[0]))])
        elif len(context.args) == 0:
            self.gs.send_messages([game.Message(chat_id, GetText("Please specify the number of rounds."))])
        else:
            self.gs.send_messages([game.Message(chat_id, GetText("Don't you think these are too many parameters?"))])

    def set_display_name(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            keyboard = [[InlineKeyboardButton(v, callback_data=k) for k, v in BOOL.items()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text('Do you want to see the authors names in the result?', reply_markup=reply_markup)
        else:
            self.gs.send_messages([game.Message(update.message.chat.id,
                                                GetText("Games can only be edited in group chats."))])

    def set_sync(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.message.chat.type == "private":
            self.gs.send_messages([game.Message(update.message.chat.id,
                                                GetText("Games can only be edited in group chats."))])
            return
        else:
            keyboard = [[InlineKeyboardButton(v, callback_data=k)
                         for k, v in SYNC.items()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text('Please choose:', reply_markup=reply_markup)

    def set_language(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        keyboard = [[InlineKeyboardButton(v, callback_data=k)
                     for k, v in LANGUAGES.items()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Please choose:', reply_markup=reply_markup)

    def button(self, update, context):
        query = update.callback_query
        print(query)
        command = query.message.reply_to_message.text
        if command == f'/{game.COMMAND_SET_LANGUAGE}@qaqagamebot' or \
                command == f'/{game.COMMAND_SET_LANGUAGE}':
            query.edit_message_text(text="Chosen language: {}".format(LANGUAGES.get(query.data, '–')))
            self.gs.set_chat_locale(query.message.chat.id, query.data)
        elif command == f"/{game.COMMAND_SET_DISPLAY_NAME}@qaqagamebot" or \
                command == f"/{game.COMMAND_SET_DISPLAY_NAME}":
            query.edit_message_text(text="Display the names: {}".format(BOOL.get(query.data, '–')))
            if query.data == "True":
                self.gs.set_show_result_names(query.message.chat.id, True)
            elif query.data == "False":
                self.gs.set_show_result_names(query.message.chat.id, False)
            else:
                query.edit_message_text(text="Oh no! 😱 There's a problem!")
        elif command == f"/{game.COMMAND_SET_SYNC}@qaqagamebot" or \
                command == f"/{game.COMMAND_SET_SYNC}":
            query.edit_message_text(text="Chosen mode: {}".format(SYNC.get(query.data, '–')))
            if query.data == "True":
                self.gs.set_synchronous(query.message.chat.id, True)
            elif query.data == "False":
                self.gs.set_synchronous(query.message.chat.id, False)
            else:
                query.edit_message_text(text="Oh no! 😱 There's a problem!")
        else:
            query.edit_message_text(text="Oh no! 😱 There's a problem!")


    def help(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Print explanation of the game and commands."""
        pass  # TODO implement

    def status(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Print info about game states and sheets."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "private":
            self.gs.get_user_status(chat_id)
        else:
            self.gs.get_group_status(chat_id)

    def send_messages(self, messages: List[game.TranslatedMessage]) -> None:
        """Send the messages to the corporated chat ids."""
        for msg in messages:
            chat_id, text = msg
            self.updater.bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

    def error(self, update, context) -> None:
        """Log errors caused by updates."""
        logger.error('Error while update %s', update, exc_info=context.error)
