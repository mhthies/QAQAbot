import toml

from qaqa_bot.bot import Frontend


def main():
    config = toml.load("config.toml")
    frontend = Frontend(config)
    frontend.start_bot()
    print(frontend.set_commands())


if __name__ == '__main__':
    main()
