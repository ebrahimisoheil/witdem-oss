"""Run the Product Factory workload example with one command."""

from dotenv import load_dotenv
from product_factory_app.cli import research_command

load_dotenv()


if __name__ == "__main__":
    research_command()
