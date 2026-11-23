from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    # Deliberately loose: we only look the address up, so strict RFC/deliverability
    # checks would just reject valid internal domains (e.g. *.test, *.local).
    email = EmailField("Email address", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=256)])
    remember = BooleanField("Keep me signed in")
