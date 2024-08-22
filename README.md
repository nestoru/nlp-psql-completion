# NPL PSQL Completion
Project nlp-psql-completion aims at providing a generic way to fill up postgresql database tables by interacting with end users with English as a natural language. It uses ChatGPT as LLM for Natural Language Processing (NLP) to interpret end user needs and PostgreSQL (PSQL) as the database engine where such needs are stored; however this proof of concept should be good enough to perform the same operations with any LLM and any database (SQL or NoSQL).

## Process Flow Diagram
```
+----------------------------+      +----------------------------+      +------------------------------+
|        User Input          |      |        Flask API           |      |      Message Processing      |
| (User sends a message)     | ---> | Receives and processes     | ---> | Extracts ID and relevant     |
|                            |      | user input messages        |      | data from the user message   |
+----------------------------+      +----------------------------+      +------------------------------+
                                                                                        |
                                                                                        |
             ---------------------------------------------------------------------------
            |                         
            v                            
+----------------------------+      +----------------------------+      +-----------------------------+
|    Database Query Builder  |      |     PostgreSQL Database    |      |     OpenAI API Call         |
| Constructs SQL query based | ---> | Executes query, fetches    | ---> | Sends current data and      |
| on relationships in config |      | current records            |      | user message to OpenAI      |
+----------------------------+      +----------------------------+      +-----------------------------+
                                                                                        |
                                                                                        |
             ---------------------------------------------------------------------------
            |
            v 
+----------------------------+      +----------------------------+      +------------------------------+
|  Process AI Response       |      |   Database Update Handler  |      |   Re-fetch Updated Record    |
| Maps AI-generated updates  | ---> | Updates appropriate tables | ---> | Confirms updates and logs    |
| to database columns        |      | and fields based on mapping|      | the final record             |
+----------------------------+      +----------------------------+      +------------------------------+
                                                                                        |
                                                                                        |
             ---------------------------------------------------------------------------
            |
            v
+---------------------------+      +-----------------------------+
|   Generate User Response  |      |       User Output           |
| Sends final message back  | ---> |  Displays updated data to   |
| to the user with updates  |      |  the user                   |
+---------------------------+      +-----------------------------+
```

## Preconditions
- The actual config.json should not be committed to to the repo for security reasons.

- python environment: before running the app do always make sure you run it in an isolated environment
```
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools
```
- Install dependencies
```
pip install -r requirements.txt
```
Note that you still have to download the spacy model.
```
python -m spacy download python -m spacy download en_core_web_sm
```
- To deactivate that environment run the below
```
deactivate
rm -rf venv
```

## Testing the solution
For testing purposes we are providing, via config.json, a scenario where we want to manage a small library system where users can borrow books. The system should track user information, books, and the borrow/return transactions. We will create a POC using a config.json relationships node with tables such as users, books, and transactions, all within a testing schema named library.

- Create library.users table
```
CREATE TABLE library.users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    email VARCHAR(255)
);
```
- Create library.books table
```
CREATE TABLE library.books (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255),
    author VARCHAR(255)
);
```
- Create library.transactions table
```
CREATE TABLE library.transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES library.users(id),
    book_id INTEGER REFERENCES library.books(id),
    borrow_date DATE,
    return_date DATE
);
```
- Insert into library.users
```
INSERT INTO library.users (name, email) VALUES
('John Doe', 'john.doe@example.com'),
('Jane Smith', 'jane.smith@example.com');
```
- Insert into library.books
```
INSERT INTO library.books (title, author) VALUES
('The Great Gatsby', 'F. Scott Fitzgerald'),
('To Kill a Mockingbird', 'Harper Lee');
```
- Insert into library.transactions
```
INSERT INTO library.transactions (user_id, book_id, borrow_date, return_date) VALUES
(1, 1, '2024-08-01', NULL),
(2, 2, '2024-08-02', '2024-08-10');
```

In order to test the solution we use curl:

A. User asks for their borrowed book information:
```
curl -X POST "http://127.0.0.1:5000/message?id_name=library.transactions.id&id_value=1" \
     -H "Content-Type: application/json" \
     -d '{"message": "Can you provide me with my borrowed book details?"}'
```
B. User wants to update the borrow date:
```
curl -X POST "http://127.0.0.1:5000/message?id_name=library.transactions.id&id_value=1" \
     -H "Content-Type: application/json" \
     -d '{"message": "The borrow date should be 2024-08-05."}'
```
C. User wants to update the return date:
```
curl -X POST "http://127.0.0.1:5000/message?id_name=library.transactions.id&id_value=1" \
     -H "Content-Type: application/json" \
     -d '{"message": "I returned the book on 2024-08-15."}'
```
D. User changes the email address:
```
curl -X POST "http://127.0.0.1:5000/message?id_name=library.transactions.id&id_value=1" \
     -H "Content-Type: application/json" \
     -d '{"message": "My email should be john.newemail@example.com"}'
```
