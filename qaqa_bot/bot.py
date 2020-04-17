import telegram
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters,
                          ConversationHandler)
import logging
import toml

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

config = toml.load("config.toml")


def start(update, context) -> None:
    """Send a friendly welcome message."""
    context.bot.send_message(chat_id=update.effective_chat.id, text="So you want to play question-answer-question-"
                                                                    "answer telephone with us? Great.")


def error(update, context) -> None:
    """Log errors caused by updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def init_bot(token: str) -> None:
    updater = Updater(token=token, use_context=True)
    dispatcher = updater.dispatcher

    # All the handlers
    start_handler = CommandHandler('start', start)
    dispatcher.add_handler(start_handler)

    # errorhandling
    dispatcher.add_error_handler(error)

    # Go go go
    updater.start_polling()


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    init_bot(config["bot"]["api_key"])

