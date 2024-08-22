from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
import logging
from datetime import datetime, date, time
from decimal import Decimal
import json
from collections import OrderedDict
import requests

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load configuration
with open("config.json") as f:
    config = json.load(f)

DATABASE_URL = f"postgresql://{config['pguser']}:{config['pgpassword']}@{config['pghost']}:{config['pgport']}/{config['pgdatabase']}"
RELATIONSHIPS = config["relationships"]

# Setup database connection
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

# OpenAI API configuration
openai_api_key = config['openai_api_key']
openai_api_url = "https://api.openai.com/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {openai_api_key}"
}

# Utility functions
def serialize(obj):
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj

def map_fields_to_columns(record, relationships):
    """Map the fields in the JSON response back to the actual database column names based on the relationships config."""
    mapped_columns = {}

    for table, columns in relationships.items():
        mapped_columns[table] = {}

        # Direct fields under the parent node
        if 'alias' in columns and columns['alias'] in record:
            mapped_columns[table][columns['alias']] = record[columns['alias']]

        # Fields within the child nodes
        if 'fields' in columns:
            for field, field_info in columns['fields'].items():
                alias = field_info['alias']
                if alias in record:
                    mapped_columns[table][field] = record[alias]

    logger.debug(f"Mapped columns: {json.dumps(mapped_columns, indent=2)}")
    return mapped_columns

def construct_query(table, id_name, id_value):
    """Construct the SQL query to retrieve the data based on the relationships in the config."""
    logger.info(f"Constructing query for table {table} with id {id_name} and value {id_value}")
    schema, table_name = table.split('.')

    columns = []
    alias_counter = 1
    relationships = RELATIONSHIPS[table]
    for column, ref_table_info in relationships.items():
        if 'ref' in ref_table_info:  # Handling child nodes
            ref_schema, ref_table, ref_column = ref_table_info['ref'].split('.')
            ref_alias = f"{ref_table}_alias_{alias_counter}"
            alias_counter += 1
            ref_columns = [f"{ref_alias}.{ref_col} AS \"{ref_col_info['alias']}\"" for ref_col, ref_col_info in ref_table_info['fields'].items()]
            columns.extend(ref_columns)
            logger.debug(f"Mapping child: {column} -> {ref_table_info['ref']} with fields {ref_columns}")
        else:  # Handling direct fields under parent node
            columns.append(f"{schema}.{table_name}.{column} AS \"{ref_table_info['alias']}\"")
            logger.debug(f"Mapping parent: {column} -> {schema}.{table_name}.{column} with alias {ref_table_info['alias']}")

    query = f"SELECT {', '.join(columns)} FROM {schema}.{table_name}"

    join_clauses = []
    alias_counter = 1
    for column, ref_table_info in relationships.items():
        if 'ref' in ref_table_info:  # Only join on child nodes
            ref_schema, ref_table, ref_column = ref_table_info['ref'].split('.')
            ref_alias = f"{ref_table}_alias_{alias_counter}"
            alias_counter += 1
            join_clauses.append(f"LEFT JOIN {ref_schema}.{ref_table} AS {ref_alias} ON {schema}.{table_name}.{column} = {ref_alias}.{ref_column}")

    if join_clauses:
        query += " " + " ".join(join_clauses)

    # Extract just the column name from id_name (removing the fully qualified name)
    column_name = id_name.split('.')[-1]
    query += f" WHERE {schema}.{table_name}.{column_name} = :id_value"

    logger.info(f"Constructed query: {query}")
    return text(query)

def process_message(session, table, id_name, id_value):
    query = construct_query(table, id_name, id_value)
    try:
        result = session.execute(query, {'id_value': id_value}).mappings().fetchone()
        if not result:
            return None, "No record found."

        ordered_record = OrderedDict()
        relationships = RELATIONSHIPS[table]
        for column, relationship in relationships.items():
            if 'alias' in relationship and relationship['alias'] in result:
                ordered_record[relationship['alias']] = serialize(result[relationship['alias']])
            if 'fields' in relationship:
                for field, field_info in relationship['fields'].items():
                    alias = field_info['alias']
                    ordered_record[alias] = serialize(result[alias])

        logger.debug(f"Ordered record: {ordered_record}")
        return ordered_record, None
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        return None, "Database error."

def update_database(session, table_name, id_name, id_value, updated_record, parent_result=None):
    mapped_columns = map_fields_to_columns(updated_record, RELATIONSHIPS[table_name])

    try:
        # Start by fetching the main table's record to identify related records
        schema, main_table = table_name.split('.')
        primary_key_column = id_name.split('.')[-1]

        if parent_result is None:
            fetch_query = f"SELECT * FROM {schema}.{main_table} WHERE {primary_key_column} = :id_value"
            result = session.execute(text(fetch_query), {'id_value': id_value}).mappings().fetchone()
        else:
            result = parent_result

        if not result:
            logger.error(f"No record found in {table_name} with {primary_key_column} = {id_value}")
            return False

        # Dictionary to hold all updates for each related table
        updates_per_table = {}

        # Iterate over each related table defined in the relationships
        for column, ref_table_info in RELATIONSHIPS[table_name].items():
            ref_table = ref_table_info['ref']
            related_schema, related_table, related_column = ref_table.split('.')
            related_table_name = f"{related_schema}.{related_table}"

            # Get the foreign key value from the main table's record
            foreign_key_value = result.get(column)  # Access using .get() to avoid KeyError

            if foreign_key_value is None:
                logger.error(f"No related record found in {related_table_name} for foreign key {column}")
                continue  # Skip updating this table

            # Collect updates for each related table
            if related_table_name not in updates_per_table:
                updates_per_table[related_table_name] = []

            # Get the fields to update in the related table
            if column in mapped_columns:
                updates_per_table[related_table_name].append({
                    'columns': mapped_columns[column],
                    'related_id': foreign_key_value,
                    'related_column': related_column
                })

            # Check for nested relationships and recursively update them
            if related_table_name in RELATIONSHIPS:
                nested_success = update_database(
                    session,
                    related_table_name,
                    f"{related_table}.{related_column}",
                    foreign_key_value,
                    updated_record,
                    parent_result=result
                )

                if not nested_success:
                    logger.error(f"Failed to update nested relationships for {related_table_name}")

        # Execute updates for each related table
        for related_table_name, updates in updates_per_table.items():
            for update_info in updates:
                columns = update_info['columns']
                related_id = update_info['related_id']
                related_column = update_info['related_column']

                if columns:
                    update_query = f"""
                    UPDATE {related_table_name}
                    SET {', '.join([f"{key} = :{key}" for key in columns.keys()])}
                    WHERE {related_column} = :related_id
                    """

                    # Log the SQL query and parameters for debugging
                    logger.debug(f"Executing SQL on table {related_table_name}: {update_query}")
                    logger.debug(f"With parameters: {columns}")

                    # Add the related_id to the parameters
                    columns['related_id'] = related_id

                    # Execute the update
                    session.execute(text(update_query), columns)

        # Commit the transaction after all updates
        session.commit()

        # Re-fetch the record to ensure the update was successful
        updated_result = session.execute(text(fetch_query), {'id_value': id_value}).mappings().fetchone()
        logger.debug(f"Updated record after commit: {updated_result}")

        logger.debug(f"Successfully updated records related to {table_name} with {primary_key_column} = {id_value}")
        return True
    except SQLAlchemyError as e:
        logger.error(f"Failed to update the record: {e}")
        session.rollback()
        return False

def map_corrections_using_gpt(record, user_message):
    prompt = f"""
    The current database record is:
    {json.dumps(record, indent=2)}

    The user provided the following input: '{user_message}'.

    Please update the JSON record to reflect any changes based on the user's input.
    **Return only the updated JSON record. If no changes are needed, return the original JSON record without any additional text.**
    """

    payload = {
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    logger.debug(f"Sending payload to OpenAI: {json.dumps(payload, indent=2)}")

    response = requests.post(openai_api_url, headers=headers, data=json.dumps(payload))

    if response.status_code == 200:
        corrections_text = response.json()['choices'][0]['message']['content'].strip()
        logger.debug(f"Received response from OpenAI: {corrections_text}")

        try:
            corrections = json.loads(corrections_text)
            if isinstance(corrections, dict):  # Check if the response is a valid JSON object
                return corrections, None
            else:
                logger.error("Received non-JSON object from OpenAI")
                return None, "Received non-JSON object"
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON response from OpenAI")
            return None, "Failed to parse corrections"
    else:
        logger.error(f"OpenAI API request failed with status code {response.status_code}: {response.text}")
        return None, f"OpenAI API request failed: {response.status_code}"

def generate_response(record):
    response_text = "I have now in my records the following: " + ". ".join(
        f"{key.capitalize().replace('_', ' ')} is {value}" for key, value in record.items() if value is not None
    ) + ". If you need modifications, please state them. Otherwise, we thank you for completing your record."
    return response_text

@app.route("/message", methods=["POST"])
def message():
    data = request.json
    user_message = data.get("message")
    id_name = request.args.get("id_name")
    id_value = request.args.get("id_value")
    table_name = "bookings.transfer_services"
    
    if not id_name or not id_value:
        return jsonify({"response": "id_name and id_value are required."}), 400
    
    logger.info(f"Received message: {user_message} with id_name: {id_name} and id_value: {id_value}")
    
    session = Session()
    current_record, error = process_message(session, table_name, id_name, id_value)
    
    if error:
        session.close()
        return jsonify({"response": error}), 500
    if not current_record:
        session.close()
        return jsonify({"response": "No record found."}), 404

    # Generate corrections
    corrections, error = map_corrections_using_gpt(current_record, user_message)

    if corrections:
        # Update the database with the new record
        success = update_database(session, table_name, id_name, id_value, corrections)
        session.close()
        
        if success:
            # Respond with the updated record
            updated_record = {**current_record, **corrections}
            response_text = generate_response(updated_record)
            return jsonify({"response": response_text})
        else:
            return jsonify({"response": "Failed to update the record in the database."}), 500

    else:
        session.close()
        if error:
            return jsonify({"response": "Failed to process the corrections. Please try again."}), 500
        else:
            # No corrections were made, so respond with the current record
            response_text = generate_response(current_record)
            return jsonify({"response": response_text})

if __name__ == '__main__':
    app.run()

