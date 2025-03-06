import sys
import argparse
import xml.etree.ElementTree as ET
from collections import OrderedDict

def generate_sql(xml_file):
    try:
        tree = ET.parse(xml_file)
    except Exception as e:
        sys.exit(f"Error parsing XML file: {e}")
    root = tree.getroot()
    sql_statements = []

    # Begin transaction
    sql_statements.append("BEGIN;")

    for command in root:
        if command.tag == 'createExtension':
            # Handle extension creation
            extension_name = command.attrib.get('name')
            if not extension_name:
                sys.stderr.write("Warning: <createExtension> without a name attribute.\n")
                continue
            sql_statements.append(f"CREATE EXTENSION IF NOT EXISTS \"{extension_name}\";")
            
        elif command.tag == 'addTable':
            table_name = command.attrib.get('name')
            if not table_name:
                sys.stderr.write("Warning: <addTable> without a name attribute.\n")
                continue

            # Check for history logging
            is_history = command.attrib.get('history', '').lower() in ['true', 'yes', '1']

            # Lists for ALTER operations and foreign keys; use an ordered dict for effective columns
            alter_ops = []
            foreign_keys = []
            effective_columns = OrderedDict()

            # Process child elements of <addTable>
            for child in command:
                if child.tag == 'addColumn':
                    col_name = child.attrib.get('name')
                    col_type = child.attrib.get('type')
                    if not col_name or not col_type:
                        sys.stderr.write(f"Warning: <addColumn> missing name or type in table {table_name}.\n")
                        continue

                    # Build the column definition
                    col_def_parts = [f"{col_name} {col_type}"]
                    if child.attrib.get('primaryKey', 'false').lower() == 'true':
                        col_def_parts.append("PRIMARY KEY")
                    # If nullable is provided and explicitly false, add NOT NULL
                    if child.attrib.get('nullable', 'true').lower() == 'false':
                        col_def_parts.append("NOT NULL")
                    if child.attrib.get('default') is not None:
                        col_def_parts.append(f"DEFAULT {child.attrib.get('default')}")
                    col_def = " ".join(col_def_parts)
                    effective_columns[col_name] = col_def

                    # Generate an ALTER TABLE command to add the column if it doesn't exist
                    alter_ops.append(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_def};")

                    # Check for unique attribute and add UNIQUE if true
                    if child.attrib.get('unique', 'false').lower() == 'true':
                         # Use provided constraint name or default pattern: uk_<table>_<column>
                        constraint_name = f"uk_{table_name}_{col_name}"
                        # Drop the constraint if it already exists
                        drop_stmt = f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name};"
                        alter_ops.append(drop_stmt)
                        # Create the unique constraint
                        unique_stmt = f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} UNIQUE ({col_name});"
                        alter_ops.append(unique_stmt)

                elif child.tag == 'removeColumn':
                    col_name = child.attrib.get('name')
                    if not col_name:
                        sys.stderr.write(f"Warning: <removeColumn> missing name in table {table_name}.\n")
                        continue
                    if col_name in effective_columns:
                        del effective_columns[col_name]
                    alter_ops.append(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS {col_name};")

                elif child.tag == 'addForeignKey':
                    # Process foreign key constraint
                    fk_col = child.attrib.get('column')
                    ref_table = child.attrib.get('refTable')
                    ref_column = child.attrib.get('refColumn')
                    if not fk_col or not ref_table or not ref_column:
                        sys.stderr.write(f"Warning: <addForeignKey> missing required attributes in table {table_name}.\n")
                        continue
                    # Drop the constraint if it already exists to avoid duplicates
                    drop_stmt = f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS fk_{table_name}_{fk_col};"
                    sql_statements.append(drop_stmt)
                    # Create the constraint
                    stmt = f"ALTER TABLE {table_name} ADD CONSTRAINT fk_{table_name}_{fk_col} FOREIGN KEY ({fk_col}) REFERENCES {ref_table}({ref_column})"
                    # Optionally add ON DELETE and ON UPDATE clauses
                    if child.attrib.get('onDelete'):
                        stmt += f" ON DELETE {child.attrib.get('onDelete')}"
                    if child.attrib.get('onUpdate'):
                        stmt += f" ON UPDATE {child.attrib.get('onUpdate')}"
                    stmt += ";"
                    foreign_keys.append(stmt)


                elif child.tag == 'addIndex':
                    index_name = child.attrib.get('name')
                    columns = child.attrib.get('columns')
                    update_flag = child.attrib.get('update', 'false').lower() == 'true'
                    if not index_name or not columns:
                        sys.stderr.write(f"Warning: <addIndex> missing required attributes in table {table_name}.\n")
                        continue
                    prefixed_index_name = f"INDEX_{index_name}"
                    if update_flag:
                        drop_stmt = f"DROP INDEX IF EXISTS {prefixed_index_name};"
                        alter_ops.append(drop_stmt)
                        create_stmt = f"CREATE INDEX {prefixed_index_name} ON {table_name} ({columns});"
                    else:
                        create_stmt = f"CREATE INDEX IF NOT EXISTS {prefixed_index_name} ON {table_name} ({columns});"
                    alter_ops.append(create_stmt)

                elif child.tag == 'removeIndex':
                    index_name = child.attrib.get('name')
                    if not index_name:
                        sys.stderr.write(f"Warning: <removeIndex> missing required name attribute in table {table_name}.\n")
                        continue
                    prefixed_index_name = f"INDEX_{index_name}"
                    alter_ops.append(f"DROP INDEX IF EXISTS {prefixed_index_name};")

                else:
                    sys.stderr.write(f"Warning: Unrecognized element '{child.tag}' in <addTable> for table {table_name}.\n")

            # If we have any effective columns, generate a CREATE TABLE statement
            if effective_columns:
                col_defs = ",\n    ".join(effective_columns.values())
                create_stmt = f"CREATE TABLE IF NOT EXISTS {table_name} (\n    {col_defs}\n);"
                sql_statements.append(create_stmt)

            # Append ALTER TABLE commands
            sql_statements.extend(alter_ops)
            # Append foreign key constraints
            sql_statements.extend(foreign_keys)

            # If history logging is enabled, create the history table and triggers
            if is_history:
                hist_table = f"History_{table_name}"
                # Create history table based on the main table
                sql_statements.append(f"CREATE TABLE IF NOT EXISTS {hist_table} (LIKE {table_name} EXCLUDING CONSTRAINTS);")
                
                # Generate ALTER TABLE commands to add new columns for history table in case main table has been updated with new columns.
                for col_name, col_def in effective_columns.items():
                    # Skip the primary key column if needed; otherwise, add every effective column.
                    sql_statements.append(f"ALTER TABLE {hist_table} ADD COLUMN IF NOT EXISTS {col_def};")
        
                # cleanup old constraints
                for child in command:
                    if child.tag == 'addColumn' and child.attrib.get('primaryKey', 'false').lower() == 'true':
                        sql_statements.append(f"ALTER TABLE {hist_table} DROP CONSTRAINT IF EXISTS {hist_table}_pkey;")

                # For each foreign key defined, drop the inherited foreign key constraint (assumed name: fk_{table_name}_{fk_col})
                for child in command:
                    if child.tag == 'addForeignKey':
                        fk_col = child.attrib.get('column')
                        if fk_col:
                            constraint_name = f"fk_{table_name}_{fk_col}"
                            sql_statements.append(f"ALTER TABLE {hist_table} DROP CONSTRAINT IF EXISTS {constraint_name};")

                #Add a historyid column as the primary key
                sql_statements.append(f"ALTER TABLE {hist_table} ADD COLUMN IF NOT EXISTS historyid SERIAL;")
                sql_statements.append(f"ALTER TABLE {hist_table} DROP CONSTRAINT IF EXISTS {hist_table}_historyid_pkey;")
                sql_statements.append(f"ALTER TABLE {hist_table} ADD CONSTRAINT {hist_table}_historyid_pkey PRIMARY KEY (historyid);")
                # Add extra columns for tracking changes
                sql_statements.append(f"ALTER TABLE {hist_table} ADD COLUMN IF NOT EXISTS changed_at TIMESTAMP DEFAULT now();")
                sql_statements.append(f"ALTER TABLE {hist_table} ADD COLUMN IF NOT EXISTS operation TEXT;")


                # Generate a trigger function if effective columns exist
                if effective_columns:
                    col_list = ", ".join(effective_columns.keys())
                    new_values = ", ".join("NEW." + col for col in effective_columns.keys())
                    old_values = ", ".join("OLD." + col for col in effective_columns.keys())

                    trigger_function_sql = f"""CREATE OR REPLACE FUNCTION log_history_{table_name}() RETURNS trigger AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        INSERT INTO {hist_table} ({col_list}, changed_at, operation)
        VALUES ({new_values}, now(), 'INSERT');
        RETURN NEW;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO {hist_table} ({col_list}, changed_at, operation)
        VALUES ({new_values}, now(), 'UPDATE');
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        INSERT INTO {hist_table} ({col_list}, changed_at, operation)
        VALUES ({old_values}, now(), 'DELETE');
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;"""
                    sql_statements.append(trigger_function_sql)

                    sql_statements.append(f"DROP TRIGGER IF EXISTS log_history_{table_name} ON {table_name};")

                    trigger_sql = f"CREATE TRIGGER log_history_{table_name} AFTER INSERT OR UPDATE OR DELETE ON {table_name} FOR EACH ROW EXECUTE FUNCTION log_history_{table_name}();"
                    sql_statements.append(trigger_sql)
                else:
                    sys.stderr.write(f"Warning: No effective columns for table {table_name}; skipping trigger generation.\n")

        elif command.tag == 'removeTable':
            table_name = command.attrib.get('name')
            if not table_name:
                sys.stderr.write("Warning: <removeTable> without a name attribute.\n")
                continue
            sql_statements.append(f"DROP TABLE IF EXISTS {table_name};")
            sql_statements.append(f"DROP TABLE IF EXISTS History_{table_name};")
        else:
            sys.stderr.write(f"Warning: Unrecognized top-level element '{command.tag}'.\n")

    # End transaction
    sql_statements.append("COMMIT;")
    return "\n\n".join(sql_statements)
def save_to_file(content, output_file):
    try:
        with open(output_file, 'w') as f:
            f.write(content)
        print(f"SQL schema successfully written to {output_file}")
    except Exception as e:
        sys.exit(f"Error writing to output file: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate SQL schema from XML definition.')
    parser.add_argument('input_file', help='Path to the input XML schema file')
    parser.add_argument('-o', '--output', required=True, help='Path to the output SQL file')
    
    args = parser.parse_args()
    
    sql_script = generate_sql(args.input_file)
    
    # Save to specified output file
    save_to_file(sql_script, args.output)
