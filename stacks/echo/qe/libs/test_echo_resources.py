import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

from echo_resources import echo, echo_glue_db


class TestEchoResources(unittest.TestCase):
    def test_echo_default_env(self):
        os.environ.pop("ENV", None)
        os.environ.pop("ENVIRONMENT", None)
        self.assertEqual(echo("bronze-ingest"), "echo-dev-bronze-ingest")

    def test_echo_custom_env(self):
        os.environ["ENV"] = "uat"
        self.assertEqual(echo("glue-job-config"), "echo-uat-glue-job-config")

    def test_echo_glue_db(self):
        os.environ["ENV"] = "dev"
        self.assertEqual(echo_glue_db("product"), "echo_dev_silver_product")
        self.assertEqual(echo_glue_db("product", layer="gold", env="uat"), "echo_uat_gold_product")


if __name__ == "__main__":
    unittest.main()
