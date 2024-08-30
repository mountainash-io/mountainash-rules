from enum import Enum
import ibis

class MatchStrategy(Enum):
    EXACT = "EXACT"
    RANGE = "RANGE"
    REGEX = "REGEX"
    # WILDCARD = "WILDCARD"
    # FUZZY = "FUZZY"


class RuleConstants:

    UNKNOWN = "<NA>"
    NOT_SET = "<NOT_SET>"

    UNKNOWN_NUMERIC = -999999999
    NOT_SET_NUMERIC = -999999998


    @classmethod
    def UNKNOWN_IBIS(cls) -> ibis.Scalar:
        return ibis.literal(cls.UNKNOWN)

    @classmethod
    def NOT_SET_IBIS(cls) -> ibis.Scalar:
        return ibis.literal(cls.NOT_SET)


    @classmethod
    def UNKNOWN_NUMERIC_IBIS(cls) -> ibis.Scalar:
        return ibis.literal(cls.UNKNOWN_NUMERIC)

    @classmethod
    def NOT_SET_NUMERIC_IBIS(cls) -> ibis.Scalar:
        return ibis.literal(cls.NOT_SET_NUMERIC)



class RuleTrinaryFlags:

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5

    @classmethod
    def PRIME_TRUE_IBIS(cls) -> ibis.Scalar:
        return ibis.literal(cls.PRIME_TRUE)

    @classmethod
    def PRIME_FALSE_IBIS(cls)-> ibis.Scalar:
        return ibis.literal(cls.PRIME_FALSE)

    @classmethod
    def PRIME_UNKNOWN_IBIS(cls)-> ibis.Scalar:
        return ibis.literal(cls.PRIME_UNKNOWN)
