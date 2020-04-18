import logging
from typing import List

import telegram
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters)

from qaqa_bot.game import GameServer, Message

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

        # Commandhandler
        start_handler = CommandHandler('start', self.start)
        self.dispatcher.add_handler(start_handler)

        # Messagehandler
        message_handler = MessageHandler(filters=Filters.text, callback=self.incoming_message)  # TODO test the filter
        self.dispatcher.add_handler(message_handler)

        # Errorhandling
        self.dispatcher.add_error_handler(self.error)

        # Gameserver
        self.gs = GameServer(config=config, send_callback=self.send_messages)

    def start_bot(self):
        """Polls for user interaction."""
        self.updater.start_polling()

    def start(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Send a friendly welcome message."""
        self.send_messages([(update.effective_chat.id, "Hi, nice to play with you")])

    def incoming_message(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Parse a text-message that was send to the bot in a private chat."""
        logger.info(msg=f"Got message from {update.message.chat.first_name}: {update.message.text}")


    def send_messages(self, messages: List[Message]) -> None:
        """Send messages"""
        for msg in messages:
            chat_id, text = msg
            self.updater.bot.send_message(chat_id=chat_id, text=text)

    def error(self, update, context) -> None:
        """Log errors caused by updates."""
        logger.warning('Update "%s" caused error "%s"', update, context.error)
