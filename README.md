# RoboHashira Project

This project is a Discord bot written in Python using the discord.py library.
The Bot is designed as a Music Bot with a variety of featueres, such as Temporary Voice Channels.

## Prerequisites

Before running the bot, make sure you have the following installed:

- Python >=3.12: [Download Python](https://www.python.org/downloads/)
- PostgreSQL: [Download PostgreSQL](https://www.postgresql.org/download/)

## Installation

1. **Clone the repository:**

```bash
git clone https://github.com/klappstuhlpy/RoboHashira.git
```

2. **Install the required Python packages:**

```bash
pip install -r requirements.txt
```

3. **Create a PostgreSQL database for the bot:**

- Launch the PostgreSQL command-line interface.
- Run the following command to create a new database:

```sql
CREATE ROLE rhashira WITH LOGIN PASSWORD 'password';
CREATE DATABASE rhashira OWNER percy;
CREATE EXTENSION pg_trgm;
```

3.5 **Configuration of database**

To configure the PostgreSQL database for use by the bot, go to the directory where `launcher.py` is located, and run the script by doing `python3.11 launcher.py db init`

4**Configure the bot:**

- Setup a ``config.py`` File:

```py
from types import SimpleNamespace

client_id = 0  # Bot ID
client_secret = ''  # Bot secret
token = ''  # Bot token

topgg_key = ''  # Top.gg API Key
mystbin_key = ''  # Mystbin API Key
dbots_key = ''  # Discord Bots List

postgresql = ''  # Your Postgresql connection string

stat_webhook = ('', '')  # Webhook for discord channel for stats

genius = SimpleNamespace(access_token='')  # For lyrics feature
wavelink = SimpleNamespace(url='', password='')
```

## License

This project is licensed under the MPL License. See the LICENSE file for details.
This Project utilizes Code from [R. Danny](https://github.com/Rapptz/RoboDanny)
