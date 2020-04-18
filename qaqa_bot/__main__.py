import toml

from qaqa_bot.bot import Frontend


def main():
    config = toml.load("config.toml")
    frontend = Frontend(config)
    frontend.start_bot()


if __name__ == '__main__':
    main()
