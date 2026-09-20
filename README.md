# NetherGames Tournament Management System

This project is a proposed PWA for NetherGames that automates the manual parts of LiveOps work, with a focus on tournament tracking and reward handling. The goal is to reduce repetitive staff effort while still keeping sensitive actions under human approval.

## Problem Definition

- Tournament rewards are filtered, validated, and distributed manually, which is slow and inconsistent.
- Tournament data exists, but it is not packaged or checked efficiently for manager review.
- LiveOps work is highly manual overall, which reduces staff motivation for routine tasks.

## Enterprise Summary

NetherGames is one of the largest non-featured Minecraft Bedrock networks, with over six million registered players. Its LiveOps are supported by a large staff team, many of whom are volunteers handling moderation and player experience responsibilities.

The main challenge is that tournament operations still rely on manual checks, manual data packaging, and manual approvals. This creates delays, increases the chance of mistakes, and makes routine work less sustainable for staff.

## Proposed Idea

A PWA solution that uses preexisting API data to automate the repetitive parts of tournament operations while keeping manager and admin approval in the workflow.

### Core Features

- Three roles: Staff, Manager, Admin
- Encrypted storage of staff data
- Semi-structured intelligent system for assisting LiveOps workflows
- Tournament scheduling to support forecasting ahead of time
- Discord announcements for tournament updates and victor declarations through webhooks
- Data packaging of victor information such as XUID and IGN for manager approval
- Disqualification filtering to exclude ineligible players from the victor pool
- Tournament history storage for backup, late assignment, and reference

## Success Criteria

Tournament operations will be considered successful if the system:

- Reduces staff load by automating repetitive tournament tasks
- Keeps tournaments on schedule with fewer delays
- Speeds up reward processing by preparing victor data for approval more efficiently
- Preserves tournament history for analysis and late assignment support
- Improves operational consistency and reduces dependence on manual effort

# How to Run the Code

If you want to look around the **public version** of the website, head to https://ng-tournies.onrender.com/. However, since this version is hooked up to a live database with real integration with the enterprise, you will be unable to access admin accounts or tournament editing features.

**PLEASE NOTE**: This application is no longer used due to the [server closure](https://ngmc.co/closure) on the 28th of June, 2026. The public version of the website (previously at `https://ng-tournies.onrender.com/`) was permanently taken offline on the 20th of September, 2026, however, there is a [waybackmachine image of the website available here](https://web.archive.org/web/20260919165839/https://ng-tournies.onrender.com/).
Some feature may no longer work after the 25th of July, 2026, due to the removal of the NetherGames API. This project will no longer be maintained. The author of this upholds no responsibility for any issues that arise from using this project, and urges users to use it at their own risk. The project is provided as-is for educational purposes only.

If you want to test out the full functionality of the website, you can run a local version on your machine by following the instructions below.

## Requirements

- Python 3.10-3.13 (3.13 recommended) + pip
- Virtual environment (optional but recommended)
- Discord server and webhook URL for testing announcements (required for account verification and announcement features, but you can use the same webhook for both).
- [NetherGames API key](https://portal.nethergames.org/auth/applications) for fetching tournament and player data (required for post-tournament (leaderboard) handling)
- An Xbox account which has logged into NetherGames at least once between the 2018 and June 2026. You can check your account status by searching your in-game username on the [NetherGames Portal](https://ngmc.co/p). This is required for integrated player validation features, even if the VERIFY_STAFF_STATUS is set to False. **If you do not have a NetherGames account, you can use the test account `MegaRabyteYT` for testing purposes.** Read #restrictions below for more details.

## Setup Instructions
1. Download or clone the repository to your local machine and create a virtual environment.
2. Install the required dependencies using pip:
    ```bash
    pip install -r requirements.txt
    ```

    > PLEASE NOTE: 
    > It is recommended to use a **virtual environment** configured with Python 3.13 for the best compatibility and performance.

3. Create an `.env` file in the `app/` directory (find the `app/.env.example` file and remove the `.example` extension) and add the following content, replacing the placeholders with your actual values:
    ```
    # Create a random secret key for encrypting session data
    SECRET_KEY = abc123-def456-ghi789-jkl012

    # You may use any database URI here.
    # If you want to use a local SQLite database for testing, simply remove the `SQLALCHEMY_DATABASE_URI` line and follow the instructions in the next step (step 5). 
    SQLALCHEMY_DATABASE_URI = 'postgresql://username:password@url:port/mydatabaseuri'

    NETHERGAMES_API_KEY = nethergames.api.integration.key # Required for fetching tournament data and player validation

    # Set up Discord environment variables for announcements. Required. (You can create a test Discord server and generate a webhook for testing purposes.)
    SECURE_DISCORD_WEBHOOK_URL = https://discord.com/api/webhooks/<WEBHOOK_ID_1>/<WEBHOOK_TOKEN_1>
    ANNOUNCEMENT_DISCORD_WEBHOOK_URL = https://discord.com/api/webhooks/<WEBHOOK_ID_2>/<WEBHOOK_TOKEN_2>

    # Security settings
    VERIFY_STAFF_STATUS = False # Set to `True` to enable staff status verification against the NetherGames API, optional. False recommended for testing.
    ```
5. Create the database.
    If you are using a server-hosted database, make sure it is set up and the URI is correctly specified in the `.env` file.

    If you want to use a local SQLite database for testing:
        - Remove the `SQLALCHEMY_DATABASE_URI` line from your `.env` file.
        - Create a new file named `tourney.db` in `instance\tourney.db`. This will be your SQLite database file.

6. Configure the database.
    Run one of the following commands in your terminal to set up the database schema:
    _Windows_
    ```bash
    set FLASK_APP=run.py
    ```
    _MacOS/Linux_
    ```bash
    export FLASK_APP=run.py
    ```
    Then, run the Flask shell:
    ```bash
    flask shell
    from app import db  # Import the database instance from your application
    db.drop_all()       # This will delete all existing data in the database (if any).
    db.create_all()     # This will create the database tables based on the defined models.
    db.session.commit() # Commit the changes to the database.
    exit()              # Exits the shell instance
    ```

7. Run the Flask development server:
    ```bash
    flask run
    ```

    Open your web browser and navigate to the link specified in the console (e.g. `http://localhost:3000/`) to access the application.

## Creating your First Staff Account
This service uses a whitelisting system to avoid unauthorised access to sensitive features. As a result, you will need to whitelist an Xbox account in the database first before you can log in with it. To do this, follow the instructions below:

1. Open the database in a database management tool (e.g., DB Browser for SQLite if using SQLite, database management tools within your development environment, DBeaver for server-hosted databases, etc).
2. Insert a new record into the `whitelist` table with the following values:
    - `xuid`: The XUID of the Xbox account you want to whitelist. You can find this by searching for the account's in-game username on the [NetherGames Portal](https://ngmc.co/p) and looking for the XUID in the URL or account details.
    - `username`: The in-game username of the account. Needs to match with the associated XUID.
    - `whitelisted_at`: Set to the current timestamp or manually set it to `YYYY-MM-DD HH:MM:SS.mmm` format (e.g., `2026-06-12 08:30:00.000`).
    - `whitelisted_by`: You can leave this blank.
    - `whitelisted_by_xuid`: You can leave this blank or set it to your own XUID.
3. Use the account registration tool within the application (https://ng-tournies.onrender.com/register). Please note that an OTP code will be sent to the Secure Channel Discord webhook for verification, so make sure you have access to the Discord server associated with the webhook URL you provided in the `.env` file.
4. After registration, you should be able to see your in-game name on the left-hand sidebar, which indicates that you are logged in. To access admin features, you will need to manually change your `_role` in the `users` table of the database to `ADMIN` for your account.

After changing your role to Admin, you will have full access to the platform's features, including tournament management, announcements, and staff management. You will never need to manually edit the database again after this, as all necessary features for managing the platform are available through the web interface.

## Restrictions
Please note that this application regularily checks the NetherGames API for tournament and player data. As a result, you will not be able to create a fake account, even if the `VERIFY_STAFF_STATUS` is set to False, without also creating a corresponding account on the NetherGames network that has logged in at least once between July 2018 to June 2026. This is because the application relies on the XUID, username, and staff status data from the NetherGames API to validate accounts and provide features such as tournament tracking and announcements, and an API check is run on account creation regardless of the `VERIFY_STAFF_STATUS` value.

As a result, if you want to test the application while not being registered to the NetherGames network, you may use my profile [`MegaRabyteYT`](https://ngmc.co/p/MegaRabyteYT) for testing purposes, as it has a corresponding XUID and is eligible for access to the platform's features. You can register with the username `MegaRabyteYT` and follow the registration process to create an account linked to that profile. This will allow you to test the application's features without needing to create your own NetherGames account.

Since your instance of the application is separate from the live version, using the `MegaRabyteYT` account for testing will not affect the live platform or its data. You can safely use this account to explore and test the features of the application without any impact on the actual operations of NetherGames.

Please note that this method will not work after the 25th of July, 2026, as the NetherGames API has been taken offline. No offline-ready version of this application will be supported.

# Contact
This project was developed for a school-based assessment task and is NOT an official product of NetherGames. If you are a server owner or staff member and are interested in having this service implemented for your own server, or if you have any questions about the project, feel free to contact me on Discord at [`megarabyte` (723100946296602674)](https://discord.com/users/723100946296602674).

Thank you for taking the time to explore this project!
