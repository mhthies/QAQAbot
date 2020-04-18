
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
 
 
## Deployment

TODO requirements and installation

TODO database initialization
