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
import datetime

import telegram
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, messagequeue,
                          run_async)
from telegram.utils import promise

from . import game
from .util import GetText

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

LANGUAGES = {'lan_de': '🇩🇪',
             'lan_en': '🇬🇧'}
BOOLDIS = {"dis_yes": "Yes 👍", "dis_no": "No 👎"}
SYNC = {"syn_syn": "Synchronous mode", "syn_asyn": "Asynchronous mode"}


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
        leave_game_handler = CommandHandler(game.COMMAND_LEAVE_GAME, self.leave_game,
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
        self.dispatcher.add_handler(leave_game_handler)
        self.dispatcher.add_handler(stop_game_handler)
        self.dispatcher.add_handler(stop_game_immediately_handler)
        self.dispatcher.add_handler(set_rounds_handler)
        self.dispatcher.add_handler(set_synchronous_handler)
        self.dispatcher.add_handler(set_display_name_handler)

        # Messagehandler
        message_handler = MessageHandler(
            filters=telegram.ext.filters.MergedFilter(
                Filters.text, telegram.ext.filters.MergedFilter(~Filters.update.edited_message, ~Filters.command)),
            callback=self.incoming_message)
        self.dispatcher.add_handler(message_handler)
        message_edit_handler = MessageHandler(filters=telegram.ext.filters.MergedFilter(
            Filters.update.edited_message, ~Filters.command), callback=self.edited_message)
        self.dispatcher.add_handler(message_edit_handler)

        # Errorhandling
        self.dispatcher.add_error_handler(self.error)

        # Gameserver
        self.gs = game.GameServer(config=config)

        # Flood limits avoiding delay queue
        self._message_queue = messagequeue.MessageQueue(autostart=False)

    def set_commands(self):
        """Sends the commands to the BotFather."""
        ## debug
        # actual: List[(str,str)] = (self.updater.bot.getMyCommands())
        # print(actual[0].command)

        commands: List[BotCommand] = [
            BotCommand(game.COMMAND_HELP, "Explains the commands."),
            BotCommand(game.COMMAND_STATUS, "Displays status information about your games."),
            BotCommand(game.COMMAND_REGISTER, "Let the bot talk to you. Necessary for playing the game."),
            BotCommand(game.COMMAND_NEW_GAME,
                       "Spawns a game.Default settings: asynchronous, number of rounds is the number of players."),
            BotCommand(game.COMMAND_JOIN_GAME, "Join the current game."),
            BotCommand(game.COMMAND_LEAVE_GAME, "Leave the current game."),
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

    def run_bot(self):
        """Starts polling for user interaction and blocks until stopped by an interrupt signal.

        This method also cares about starting the (delaying) MessageQueue and stopping it on shutdown.
        """
        self._message_queue.start()
        self.updater.start_polling()
        self.updater.idle()
        self._message_queue.stop()

    @run_async
    def start(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Send a friendly welcome message with language set to locale."""
        chat_id: int = update.effective_chat.id
        lang = update.message.from_user.language_code
        if lang is not None:
            self.gs.set_chat_locale(chat_id, update.message.from_user.language_code)
        if update.message.chat.type == telegram.Chat.PRIVATE:
            name = (f"@{update.message.from_user.username}"
                    if update.message.from_user.username is not None
                    else update.message.from_user.first_name)
            self.send_messages(self.gs.register_user(update.message.chat.id, update.message.from_user.id, name))
            logger.debug(msg=f"{update.message.from_user.first_name} registered.")
        else:
            logger.debug(msg=f"Chat {chat_id} sends start-commands")

    @run_async
    def new_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == telegram.Chat.GROUP \
                or update.message.chat.type == telegram.Chat.SUPERGROUP:
            logger.debug(msg=f"Try to spawn a game in {chat_id}")
            self.send_messages(self.gs.new_game(chat_id=chat_id, name=update.message.chat.title))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Games can only be spawned in group chats."))]))

    @run_async
    def start_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Start game in current chat."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == telegram.Chat.GROUP \
                or update.message.chat.type == telegram.Chat.SUPERGROUP:
            logger.debug(msg=f"Try to start a game in {chat_id}")
            self.send_messages(self.gs.start_game(chat_id))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Games can only be started in group chats."))]))

    @run_async
    def join_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == telegram.Chat.GROUP \
                or update.message.chat.type == telegram.Chat.SUPERGROUP:
            logger.info(msg=f"{update.message.from_user} tries to join a game in {chat_id}")
            self.send_messages(self.gs.join_game(chat_id=chat_id, user_id=update.message.from_user.id))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Games can only be joined in group chats."))]))

    @run_async
    def leave_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.message.chat.type == telegram.Chat.GROUP \
                or update.message.chat.type == telegram.Chat.SUPERGROUP:
            self.send_messages(self.gs.leave_game(update.message.chat.id, update.message.from_user.id))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(update.message.chat.id, GetText("Games can only be left in group chats."))]))

    @run_async
    def incoming_message(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Parse a text-message that was send to the bot in a private chat."""
        text = update.message.text
        logger.info(msg=f"Got message from {update.message.from_user.first_name}: {text}")
        logger.info(update)
        if update.message.chat.type == telegram.Chat.PRIVATE:
            submitted_text = update.message.text_html_urled
            self.send_messages(self.gs.submit_text(update.message.chat.id, update.message.message_id, submitted_text))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(update.message.chat.id, GetText("Sorry, I do not understand. Please use a command to "
                                                              "communicate with me."))]))

    @run_async
    def edited_message(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.edited_message.chat.type == telegram.Chat.PRIVATE:
            self.send_messages(self.gs.edit_submitted_message(update.edited_message.chat.id,
                               update.edited_message.message_id,
                               update.edited_message.text))

    @run_async
    def stop_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Stop the game after the current round."""
        self.send_messages(self.gs.stop_game(chat_id=update.message.chat.id))

    @run_async
    def stop_game_immediately(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Stop the game without awaiting the end of the current round."""
        self.send_messages(self.gs.immediately_stop_game(chat_id=update.message.chat.id))

    @run_async
    def set_rounds(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Set the number of rounds"""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == telegram.Chat.PRIVATE:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Games can only edited in group chats."))]))
            return
        # Accept just one parameter and when given more or less
        if len(context.args) == 1:
            try:
                rounds: int = int(context.args[0])
                self.send_messages(self.gs.set_rounds(chat_id, rounds))
            except ValueError:
                self.send_messages(self.gs.get_translations(
                    [game.Message(chat_id, GetText("‘{arg}’ is not a number of rounds!").format(arg=context.args[0]))]))
        elif len(context.args) == 0:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Please specify the number of rounds."))]))
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(chat_id, GetText("Don't you think these are too many parameters?"))]))

    @run_async
    def set_display_name(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.message.chat.type == telegram.Chat.GROUP \
                or update.message.chat.type == telegram.Chat.SUPERGROUP:
            keyboard = [[InlineKeyboardButton(v, callback_data=k) for k, v in BOOLDIS.items()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg = GetText("Do you want to see the authors names in the result?")
            update.message.reply_text(self.gs.translate_string(msg, update.effective_chat.id),
                                      reply_markup=reply_markup)
        else:
            self.send_messages(self.gs.get_translations(
                [game.Message(update.message.chat_id, GetText("Games can only be edited in group chats."))]))

    @run_async
    def set_sync(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        if update.message.chat.type == telegram.Chat.PRIVATE:
            self.send_messages(self.gs.get_translations(
                [game.Message(update.message.chat.id, GetText("Games can only be edited in group chats."))]))
            return
        else:
            keyboard = [[InlineKeyboardButton(v, callback_data=k)
                         for k, v in SYNC.items()]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text(self.gs.translate_string(
                GetText("Please choose:"), update.effective_chat.id), reply_markup=reply_markup)

    @run_async
    def set_language(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        keyboard = [[InlineKeyboardButton(v, callback_data=k)
                     for k, v in LANGUAGES.items()]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            self.gs.translate_string(GetText("Please choose:"), update.effective_chat.id),
            reply_markup=reply_markup)

    @run_async
    def button(self, update, _context):
        query = update.callback_query
        chat_id = update.effective_chat.id
        print(query)
        button = query.data
        print("Button pressed: " + button)
        if button in LANGUAGES:
            self.gs.set_chat_locale(chat_id, button[4:], override=True)
            query.edit_message_text(text=self.gs.translate_string(
                GetText("Chosen language: {lang}").format(lang=LANGUAGES.get(button, '–')), chat_id))
        elif button in BOOLDIS:
            if button == "dis_yes":
                self.send_messages(self.gs.set_show_result_names(chat_id, True))
            elif query.data == "dis_no":
                self.send_messages(self.gs.set_show_result_names(chat_id, False))
            else:
                query.edit_message_text(text=self.gs.translate_string(
                    GetText("Oh no! 😱 There's a problem choosing a language!"), chat_id))
            query.edit_message_text(text=self.gs.translate_string(
                GetText("Display the names: {state}").format(state=BOOLDIS.get(button, '–')), chat_id))
        elif button in SYNC:
            if query.data == "syn_syn":
                self.send_messages(self.gs.set_synchronous(chat_id, True))
            elif query.data == "syn_asyn":
                self.send_messages(self.gs.set_synchronous(chat_id, False))
            else:
                query.edit_message_text(self.gs.translate_string(
                    GetText("Oh no! 😱 There's a problem choosing a mode!")), chat_id)
            query.edit_message_text(self.gs.translate_string(
                GetText("Chosen mode: {mode}").format(mode=SYNC.get(button, '–')), chat_id))
        else:
            query.edit_message_text(self.gs.translate_string(
                GetText("Oh no! 😱 There's a problem! I don't know this button *️⃣? "), chat_id))

    @run_async
    def help(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Print explanation of the game and commands."""
        pass  # TODO implement

    @run_async
    def status(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Print info about game states and sheets."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == telegram.Chat.PRIVATE:
            self.send_messages(self.gs.get_user_status(chat_id))
        else:
            self.send_messages(self.gs.get_group_status(chat_id))

    def send_messages(self, messages: List[game.TranslatedMessage]) -> None:
        """Send the messages to the corporated chat ids."""
        for msg in messages:
            chat_id, text = msg
            prom = promise.Promise(self.updater.bot.send_message, (),
                                   {'chat_id': chat_id, 'text': text, 'parse_mode': telegram.ParseMode.HTML})
            self._message_queue(prom, False)
            # TODO add limiting of group chat messages, as soon as MessageQueue supports per-group limits

    def error(self, update, context) -> None:
        """Log errors caused by updates."""
        logger.error('Error while update %s', update, exc_info=context.error)
        if update.effective_chat.id is not None:
            self.send_messages(self.gs.get_translations(
                [game.Message(update.effective_chat.id,
                              GetText("Oh no! 😱 A problem occured at {time}! \n "
                                      "Please forward this message to {owner} for help.").
                              format(time=datetime.datetime.now().isoformat(),
                                     owner=self.config["bot"]["owner_username"]))]))
