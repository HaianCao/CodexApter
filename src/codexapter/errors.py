class AdapterError(Exception):
    """Expected error that can be presented without a traceback."""


class ConfigError(AdapterError):
    pass


class CredentialError(AdapterError):
    pass


class DiscoveryError(AdapterError):
    pass


class LaunchError(AdapterError):
    pass
