from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from .terms import TERMS_VERSION

def check_password_strength(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be no more than 72 bytes.")
    if not any(char.islower() for char in value):
        raise ValueError("Password must include a lowercase letter.")
    if not any(char.isupper() for char in value):
        raise ValueError("Password must include an uppercase letter.")
    if not any(char.isdigit() for char in value):
        raise ValueError("Password must include a number.")
    if not any(not char.isalnum() for char in value):
        raise ValueError("Password must include a symbol.")
    return value


class UserRegisterSchema(BaseModel):
    full_name: str = Field(min_length=2)
    account_type: str | None = Field(default=None, pattern="^(student|external)$")
    email: EmailStr
    phone_number: str = Field(max_length=32, pattern=r"^\+?[0-9][0-9 ()-]{5,30}$")
    password: str = Field(min_length=8, max_length=72)
    confirm_password: str = Field(min_length=8, max_length=72)
    terms_accepted: bool
    terms_version: str = Field(min_length=1, max_length=32)
    hostel_or_business_address: str | None = None
    store_name: str | None = None

    @field_validator("phone_number", mode="before")
    @classmethod
    def clean_phone(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("password", "confirm_password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return check_password_strength(value)

    @model_validator(mode="after")
    def validate_registration_email(self) -> UserRegisterSchema:
        if not self.terms_accepted:
            raise ValueError("You must accept the Terms and Conditions to create an account.")
        if self.terms_version != TERMS_VERSION:
            raise ValueError("The Terms and Conditions have changed. Please review and accept the current version.")
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        allowed = ("@st.knust.edu.gh", "@idl.knust.edu.gh")
        is_student_email = str(self.email).lower().endswith(allowed)
        if self.account_type is None:
            # Signup only needs an email and password: a KNUST email makes a student account.
            self.account_type = "student" if is_student_email else "external"
        if self.account_type == "student" and not is_student_email:
            raise ValueError("Student accounts need an @st.knust.edu.gh or @idl.knust.edu.gh email.")
        if self.account_type == "external" and is_student_email:
            raise ValueError("Use a student account for KNUST student emails.")
        return self