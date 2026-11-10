from pydantic import BaseModel, EmailStr, Field, model_validator

class UserRegisterSchema(BaseModel):
    full_name: str = Field(min_length=2)
    account_type: str = Field(pattern="^(student|external)$")
    email: EmailStr
    password: str = Field(min_length=8)
    hostel_or_business_address: str | None = None
    store_name: str | None = None

    @model_validator(mode="after")
    def validate_registration_email(self) -> UserRegisterSchema:
        allowed = ("@st.knust.edu.gh", "@idl.knust.edu.gh")
        is_student_email = str(self.email).lower().endswith(allowed)
        if self.account_type == "student" and not is_student_email:
            raise ValueError("Student accounts need an @st.knust.edu.gh or @idl.knust.edu.gh email.")
        if self.account_type == "external" and is_student_email:
            raise ValueError("Use a student account for KNUST student emails.")
        return self