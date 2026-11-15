# Replaces the bench image's netbox/local_settings.py, which rebuilds DATABASES
# from the image's own env-based configuration (no ENGINE). Same DATABASES as
# netbox-branching's testing/configuration.py, with the ENGINE that NetBox's
# settings.py would otherwise have added.
from netbox_branching.utilities import DynamicSchemaDict

DATABASES = DynamicSchemaDict({
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'netbox',
        'USER': 'netbox',
        'PASSWORD': 'netbox',
        'HOST': 'localhost',
        'PORT': '',
        'CONN_MAX_AGE': 300,
    },
})

DATABASE_ROUTERS = [
    'netbox_branching.database.BranchAwareRouter',
]
