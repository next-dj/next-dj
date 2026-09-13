from django import forms

from next.forms import Form


class TaggedProfileForm(Form):
    """Form inside a zone whose body renders it through the form tag."""

    full_name = forms.CharField(max_length=50)
    email = forms.EmailField()
