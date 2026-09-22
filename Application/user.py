from dataclasses import dataclass


@dataclass
class User:
    session_token: str = ""
    customer_id: int | None = None
    admin: bool = False
    first_name: str = ""
    last_name: str = ""
    dob: str = ""
    gender: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    country: str = ""
    driving_licence_number: str = ""
    passport_number: str = ""
    licence_restrictions: str = ""

    @property
    def is_logged_in(self):
        """True once a customer_id has been set."""
        return self.customer_id is not None

    @property
    def is_admin(self):
        """True if user is admin"""
        return self.admin

    def log_out(self):
        """Reset every field back to its default."""
        self.__init__()


user = User()