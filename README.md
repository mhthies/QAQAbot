
# Telegram Question-Answer-Question-Answer game bot

A Telegram bot for playing the question-answer-question-answer party game, written in Python 3 and based on the *Python Telegram Bot* and *SQLAlchemy* libraries.

## The Game / Features

The original game is played with pen and paper:
Each player gets a sheet of paper and writes down an arbitrary question.
Then, they pass the sheet to the next player, who writes down an answer to the question and fold the sheet to hide the original question.
The sheet is passed on to the next player, the task of whom is to find a question that is answered by the given answer.
It might be the original question or not.
Sometimes not even close—nobody knows until the end of the game.
The sheet is folded again and the next player has to answer the new question—without knowing the previous questions and answers.

As soon as one of the sheets is full of questions and answers or the players no longer want to go on, all sheets are finished by making sure they end with an answer.
Then, the folding opened up, so everyone can enjoy the amusing chains of questions and answers.

To play this game in Telegram chats, this bot can be added to any number of Telegram groups.
In each group, a game can be created (`/newgame`) and joined by multiple group members (`/join`).
Each player must also start a private chat with the bot.

When the game is started (`/start_game`), the bot asks every player privately for a question.
The player answer with their question and the bot passes it privatly to the next player.
If the next player is still working on his previous sheet, all incoming sheets are queued.
This way, a player may also play several games in parallel—they are still always asked for one sheet at a time.

The bot supports synchronous and asynchronous games:
In a synchronous game (default and `/set_synchronous`), the sheets are not passed on to the next player until all players have finished writing their question/answer.
In an asynchronous game, the sheets are immediately passed on to the next player—as long as they are not busy with another sheet.

When the game is finished—either when the target number of rounds (number of players or `/set_rounds`) is reached or when manually stopped (`/stop_game`, `/immediately_stop_game`)—, the virtual sheets are presented in the group chat.

## Architecture

The bot is built on the *SQLAlchemy* ORM framework.
The object-relational datamodel is defined in `qaqa_bot.model`.

The full business logic is contained in `qaqa_bot.game`, encapsulated in the `GameServer` class.
An instance of this class holds the game config, a database `sessionmaker` to generate individual database sessions, as well as a way to send outgoing messages (in the form of a callback function).
It provides specific methods for all interaction events that update the game state in the database and send messages as required.

The interaction with Telegram is provided by the `quqa_bot.bot` module, using the *Python Telegram Bot* library.
Instances of its `Frontend` class hold an `Updater` object to interact with the Telegram API and an `GameServer` object to do the game logic.
The class has different handlers for any kind of Telegram update (esp. commands and text messages), that are automatically registered with the *Updater* on initialization and use the *GameServer's* interaction methods to carry out the actions.

Incoming updates from the Telegram API (esp. incoming messages) are handled either by the Frontend on its own or with help of the GameServer: Typically, response messages that do not require interaction with the database, are sent by the Frontend immediately. In the other case, the Frontend's handler method identifies the correct game action, determines the action's arguments from the update data, and calls the respective method of the GameServer. The response message/s are sent by the GameServer.

We use Alembic to manage database migrations.
To allow easy packaging and automatic migrations (see *Deployment* instructions below), the database versions are stored in `qaqa_bot/database_versions/`.
However, you can still use the alembic cli tool normally. 


## Deployment

### Prerequisites

For running this bot, a Python 3 environment (>= Python 3.6) with the dependencies (see `requirements.txt`) installed is required.
It is recommended to use a virtualenv environment for easier management and updating of dependencies. 

Additionally, a database in one of the DBMS supported by SQLAlchemy is required, including the appropriate Python driver library. 
See https://docs.sqlalchemy.org/en/13/dialects/index.html for a list of supported database systems and instructions.
 
For a small installation, development and testing, an SQLite database is sufficient, which can be run with Python's integrated SQLite support.
For this purpose, use `sqlite:////path/to/your/database.db` as database connection string in the bot's `config.toml`.
However, we recommend to use a proper™ database server for production use.


### Setup

Currently, the easiest setup is to clone this Git Repository and run the `qaqa_bot` python module from within it.
We may add proper Python packaging later, to make installation via pip possible. 

1. clone and enter repository
   ```bash
   git clone https://gitea.nephos.link/michael/QAQABot.git
   cd QAQABot/
   ```
2. setup virtualenv
   ```bash
   python3 -m virtualenv -p python3 venv
   ```
3. install requirements
   ```bash
   venv/bin/pip install -r requirements.txt
   ```
4. create a Telegram Bot account: follow the instructions at https://core.telegram.org/bots#creating-a-new-bot
5. create `config.toml`
   ```bash
   cp config.example.toml config.toml
   $EDITOR config.toml
   ```
   Insert
   * the bot's username,
   * the bot's API token (recived from the BotFather),
   * your personal Telegram username (will be used in the bot's description and help texts), and
   * the connection URL of your database.

### Running

Run the following command in the repository directory: 
```bash
venv/bin/python -m qaqa_bot
```

At startup, the database will be initialized or upgraded automatically.
Additionally, the Telegram bot will be configured using the Telegram API.
To avoid this behaviour, add `--no-init` to the run command.

To run the database and bot setup manually, use `--init-only`.
You may also run database migrations with Alembic for full control: `alembic upgrade head`.


### Updating

Simply update the git repository and restart the bot for updating:
```bash
git pull
venv/bin/python -m qaqa_bot
```
As explained above, the database and Telegram bot settings will be updated automatically at startup.


## License

This project is released under the Terms of the Apache License v2.0. See `NOTICE` and `LICENSE` files for more
information.


## Development

Creating a new database version:
```bash
alembic upgrade head
# do changes to model.py
alembic revision --autogenerate -m "do data things"
# revisit qaqa_bot/database_versions/*_do_data_things.py
git add qaqa_bot/model.py qaqa_bot/database_versions
```

Updating i18n translation files:
```bash
pybabel extract -k "GetText" -k "NGetText" -o qaqa_bot.pot qaqa_bot/
pybabel update -i qaqa_bot.pot -d qaqa_bot/i18n/ -l de -D qaqa_bot
```

Compiling i18n translation files:
```bash
pybabel compile -d qaqa_bot/i18n/ -l de -D qaqa_bot
```