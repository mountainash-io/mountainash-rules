from mountainash_data import BaseDataFrame

class RuleManager:

    def __init__(self, rules: BaseDataFrame):
        self.rules: BaseDataFrame = self._init_rules(rules)

    def get_rules(self) -> BaseDataFrame:
        return self.rules

    def update_rules(self, 
                     new_rules: BaseDataFrame):
        self.rules = self._init_rules(rules=new_rules)


    def _init_rules(self, 
                    rules: BaseDataFrame):
        """
        Validate the dimensions in the rule metadata.
        """

        if rules is None:
            raise ValueError("No rules specified.")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions        
        if rules.ibis_backend_schema in ["polars"]:
            rules = rules.convert_backend_schema(new_backend_schema="sqlite")

        if rules.count() == int(0):
            raise ValueError("No rules specified.")

        return rules
