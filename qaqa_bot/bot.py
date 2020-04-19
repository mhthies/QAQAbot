import logging
from typing import List
import re

import telegram
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters)

from . import game

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


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
        self.dispatcher.add_handler(help_handler)
        self.dispatcher.add_handler(status_handler)
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
        set_synchronous_handler = CommandHandler(game.COMMAND_SET_SYNCHRONOUS, self.set_synchronous,
                                                 filters=~Filters.update.edited_message)
        set_asynchronous_handler = CommandHandler(game.COMMAND_SET_ASYNCHRONOUS, self.set_asynchronous,
                                                  filters=~Filters.update.edited_message)
        self.dispatcher.add_handler(start_game_handler)
        self.dispatcher.add_handler(new_game_handler)
        self.dispatcher.add_handler(join_game_handler)
        self.dispatcher.add_handler(stop_game_handler)
        self.dispatcher.add_handler(stop_game_immediately_handler)
        self.dispatcher.add_handler(set_rounds_handler)
        self.dispatcher.add_handler(set_synchronous_handler)
        self.dispatcher.add_handler(set_asynchronous_handler)

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

    def start_bot(self):
        """Polls for user interaction."""
        self.updater.start_polling()

    def start(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Send a friendly welcome message."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "private":
            name = (f"@{update.message.from_user.username}"
                    if update.message.from_user.username is not None
                    else update.message.from_user.first_name)
            self.gs.register_user(update.message.chat.id, update.message.from_user.id, name)
            logger.debug(msg=f"Welcome {update.message.from_user.first_name}")
        else:
            logger.debug(msg=f"Chat {chat_id} sends start-commands")

    def new_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.debug(msg=f"Try to spawn a game in {chat_id}")
            self.gs.new_game(chat_id=chat_id, name=update.message.chat.title)
        else:
            self.send_messages([game.Message(chat_id, "Games can only be spawned in group chats.")])

    def start_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Start game in current chat."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.debug(msg=f"Try to start a game in {chat_id}")
            self.gs.start_game(chat_id)
        else:
            self.send_messages([game.Message(chat_id, "Games can only be started in group chats.")])

    def join_game(self, update: telegram.Update, _context: telegram.ext.CallbackContext):
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "group" or update.message.chat.type == "supergroup":
            logger.info(msg=f"{update.message.from_user} tries to join a game in {chat_id}")
            self.gs.join_game(chat_id=chat_id, user_id=update.message.from_user.id)
        else:
            self.send_messages([game.Message(chat_id, "Games can only be joined in group chats.")])

    def incoming_message(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Parse a text-message that was send to the bot in a private chat."""
        text = update.message.text
        logger.info(msg=f"Got message from {update.message.from_user.first_name}: {text}")
        logger.info(update)
        if re.search(r'\/.*', text):
            self.send_messages([game.Message(update.message.chat.id, "Sorry, this is not a valid command. 🧐")])
        else:
            self.gs.submit_text(update.message.chat.id, text)
            if update.message.entities:
                logger.info(msg=f"Got messages from {update.message.from_user.first_name}: {update.message.entities}")

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
            self.send_messages([game.Message(chat_id, "Games can only edited in group chats.")])
            return
        # Accept just one parameter and when given more or less
        if len(context.args) == 1:
            try:
                rounds: int = int(context.args[0])
                self.gs.set_rounds(chat_id, rounds)
            except ValueError:
                self.send_messages([game.Message(chat_id, f"‘{context.args[0]}’ is not a number of rounds!")])
        elif len(context.args) == 0:
            self.send_messages([game.Message(chat_id, "Please specify the number of rounds.")])
        else:
            self.send_messages([game.Message(chat_id, "Don't you think these are too many parameters?")])

    def set_synchronous(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Set the mode of the current game to synchronous (pass sheets when everyone's ready)."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "private":
            self.send_messages([game.Message(chat_id, "Games can only edited in group chats.")])
            return
        else:
            self.gs.set_synchronous(chat_id, True)

    def set_asynchronous(self, update: telegram.Update, _context: telegram.ext.CallbackContext) -> None:
        """Set the mode of the current game to asynchronous (pass sheets ASAP)."""
        chat_id: int = update.effective_chat.id
        if update.message.chat.type == "private":
            self.send_messages([game.Message(chat_id, "Games can only edited in group chats.")])
            return
        else:
            self.gs.set_synchronous(chat_id, False)

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

    def send_messages(self, messages: List[game.Message]) -> None:
        """Send the messages to the corporated chat ids."""
        for msg in messages:
            chat_id, text = msg
            self.updater.bot.send_message(chat_id=chat_id, text=text)

    def error(self, update, context) -> None:
        """Log errors caused by updates."""
        logger.error('Error while update %s', update, exc_info=context.error)
