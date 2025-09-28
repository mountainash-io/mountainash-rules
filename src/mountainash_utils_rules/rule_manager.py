# from mountainash_dataframes import BaseDataFrame

class RuleManager:

    def __init__(self, rules: BaseDataFrame):
        self.rules: BaseDataFrame = self._init_rules(rules)

    def get_rules(self) -> BaseDataFrame:
        """
        Get the rules table.

        Returns:
            BaseDataFrame: The rules table
        """
        return self.rules

    def update_rules(self,
                     new_rules: BaseDataFrame):

        """
        Update the rules table.

        Args:
            new_rules (BaseDataFrame): The new rules table

        """
        self.rules = self._init_rules(rules=new_rules)


    def _init_rules(self,
                    rules: BaseDataFrame):
        """
        Initialises the rules table.
        Checks that the rules table is not empty and is a BaseDataFrame.
        Converts the rules to a backend that supports window functions.

        Args:
            rules (BaseDataFrame): The rules table

        Returns:
            BaseDataFrame: The rules table
        """

        if rules is None:
            raise ValueError("No rules specified.")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions
        if rules.ibis_backend_schema not in ["duckdb"]:
            rules = rules.convert_backend_schema(new_backend_schema="duckdb")

        if rules.count() == int(0):
            raise ValueError("No rules specified.")

        return rules
