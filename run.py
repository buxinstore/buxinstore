import os

from app import app


def main():
    app.run(
        host=os.getenv('FLASK_RUN_HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', os.getenv('FLASK_RUN_PORT', 5000))),
        debug=os.getenv('FLASK_DEBUG', '1') == '1'
    )


if __name__ == '__main__':
    main()
