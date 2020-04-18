import telegram
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters,
                          ConversationHandler)
import logging
import toml

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


class Frontend:
    def __init__(self, config):
        self.config = config
        token: str = config["bot"]["api_key"]

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

    def start_bot(self):
        """Polls for user interaction."""
        self.updater.start_polling()

    def start(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Send a friendly welcome message."""
        context.bot.send_message(chat_id=update.effective_chat.id, text="So you want to play question-answer-question-"
                                                                        "answer telephone with us? Great.")

    def incoming_message(self, update: telegram.Update, context: telegram.ext.CallbackContext) -> None:
        """Parse a text-message that was send to the bot in a private chat."""
        logger.log(level=DEBUG, msg=update.message.text)                            # TODO warum macht das Debug-Level Fehler?

    def error(self, update, context) -> None:
        """Log errors caused by updates."""
        logger.warning('Update "%s" caused error "%s"', update, context.error)
