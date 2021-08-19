class CsobBaseException(Exception):
    pass


class CsobJSONDecodeError(CsobBaseException):
    pass


class CsobVerifyError(CsobBaseException):
    pass
