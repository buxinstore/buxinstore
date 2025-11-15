from setuptools import setup, find_packages

setup(
    name="buxinapp_store",
    version="1.0.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'flask',
        'flask-sqlalchemy',
        'flask-login',
        'flask-wtf',
        'python-dotenv',
        'werkzeug',
        'email-validator',
        'pandas',
        'pillow',
        'requests'
    ],
)
