from pathlib import Path


class Folders:
    """
    Folders management

    Attributes (may all be None):
        system: Path instance of the system folder (location where multi-users shared config is stored)
        user: Path instance of the user folder (location where multi-applications user config is stored)
        workspace: Path instance of the workspace folder (location where application config is stored)
    """

    def __init__(self, system: Path = None, user: Path = None, workspace: Path = None):
        self.__system = system
        self.__user = user
        self.__workspace = workspace

    @property
    def system(self) -> Path:
        """
        System folder
        """
        return self.__system

    @property
    def user(self) -> Path:
        """
        User folder
        """
        if self.__user is not None:
            self.__user.mkdir(parents=True, exist_ok=True)
        return self.__user

    @property
    def workspace(self) -> Path:
        """
        Workspace folder
        """
        if self.__workspace is not None:
            self.__workspace.mkdir(parents=True, exist_ok=True)
        return self.__workspace
