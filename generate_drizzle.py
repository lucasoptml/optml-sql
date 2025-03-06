import sys
import argparse
import xml.etree.ElementTree as ET
import re
from collections import OrderedDict

def pg_type_to_drizzle_type(pg_type):
    """Convert PostgreSQL types to Drizzle ORM types."""
    pg_type = pg_type.upper()
    if pg_type == 'UUID':
        return 'uuid'
    elif pg_type == 'SERIAL':
        return 'serial'
    elif pg_type.startswith('VARCHAR') or pg_type == 'TEXT':
        return 'text'
    elif pg_type.startswith('INT') or pg_type == 'INTEGER':
        return 'integer'
    elif pg_type == 'BOOLEAN':
        return 'boolean'
    elif pg_type.startswith('TIMESTAMP'):
        if 'WITH TIME ZONE' in pg_type:
            return 'timestamp', {'withTimezone': True}
        return 'timestamp'
    elif pg_type.startswith('DATE'):
        return 'date'
    elif pg_type == 'JSON':
        return 'json'
    elif pg_type == 'JSONB':
        return 'jsonb'
    elif pg_type.startswith('DECIMAL') or pg_type.startswith('NUMERIC'):
        pattern = r'(\w+)\((\d+),\s*(\d+)\)'
        match = re.match(pattern, pg_type)
        if match:
            precision = match.group(2)
            scale = match.group(3)
            return 'decimal', {'precision': precision, 'scale': scale}
        return 'decimal'
    # Add more type mappings as needed
    return 'text'  # Default fallback

def generate_drizzle_schema(xml_file):
    """Generate Drizzle ORM schema definitions from the XML schema."""
    try:
        tree = ET.parse(xml_file)
    except Exception as e:
        sys.exit(f"Error parsing XML file: {e}")
    
    root = tree.getroot()
    # Updated imports now include `sql`
    drizzle_imports = ("import { pgTable, serial, uuid, text, integer, boolean, "
                         "timestamp, date, json, jsonb, decimal, primaryKey, foreignKey } "
                         "from 'drizzle-orm/pg-core';\n"
                         "import { sql } from 'drizzle-orm';\n\n")
    table_definitions = []
    exports = []
    
    for command in root:
        if command.tag == 'addTable':
            table_name = command.attrib.get('name')
            if not table_name:
                sys.stderr.write("Warning: <addTable> without a name attribute.\n")
                continue
                
            columns = []
            foreign_keys = []
            
            for child in command:
                if child.tag == 'addColumn':
                    col_name = child.attrib.get('name')
                    col_type = child.attrib.get('type')
                    if not col_name or not col_type:
                        sys.stderr.write(f"Warning: <addColumn> missing name or type in table {table_name}.\n")
                        continue

                    # Convert PostgreSQL type to Drizzle type
                    drizzle_type_result = pg_type_to_drizzle_type(col_type)

                    if isinstance(drizzle_type_result, tuple):
                        drizzle_type, options = drizzle_type_result
                        options_str = ', '.join([
                            f"{k}: {str(v).lower() if isinstance(v, bool) else v}"
                            for k, v in options.items()
                        ])
                        type_options = f"{{ {options_str} }}"
                    else:
                        drizzle_type = drizzle_type_result
                        type_options = ""

                    col_def = f"  {col_name}: {drizzle_type}('{col_name}'"
                    if type_options:
                        col_def += f", {type_options}"
                    col_def += ")"

                    # Add nullability: if nullable is "false", add .notNull(), otherwise do nothing.
                    nullable_attr = child.attrib.get('nullable', 'true').lower()
                    if nullable_attr == 'false':
                        col_def += ".notNull()"

                    # Add default if provided
                    default_val = child.attrib.get('default')
                    if default_val is not None:
                        if default_val.startswith("sql`") and default_val.endswith("`"):
                            # Use the provided sql`...` syntax directly
                            col_def += f".default({default_val})"
                        elif default_val == "now()":
                            col_def += ".defaultNow()"
                        elif default_val == "uuid_generate_v4()":
                            col_def += ".default(sql`uuid_generate_v4()`)"
                        else:
                            # For text-like types, wrap the default value in quotes if not already quoted
                            if drizzle_type in ['text', 'varchar']:
                                if not (default_val.startswith("'") or default_val.startswith('"')):
                                    default_val = f"'{default_val}'"
                            col_def += f".default({default_val})"                    
                    
                    if child.attrib.get('primaryKey', 'false').lower() == 'true':
                        col_def += ".primaryKey()"

                    col_def += ","
                    columns.append(col_def)        

                """ disable foreign key support for now, could add it
                elif child.tag == 'addForeignKey':
                    fk_col = child.attrib.get('column')
                    ref_table = child.attrib.get('refTable')
                    ref_column = child.attrib.get('refColumn')
                    if not fk_col or not ref_table or not ref_column:
                        sys.stderr.write(f"Warning: <addForeignKey> missing required attributes in table {table_name}.\n")
                        continue
                        
                    # Build foreign key relation
                    fk_def = f"  {fk_col}Relation: foreignKey({{ columns: ['{fk_col}'], foreignColumns: ['{ref_column}'], table: '{ref_table}'"
                    
                    # Define reference options
                    if child.attrib.get('onDelete'):
                        fk_def += f", onDelete: '{child.attrib.get('onDelete')}'"
                    if child.attrib.get('onUpdate'):
                        fk_def += f", onUpdate: '{child.attrib.get('onUpdate')}'"
                    
                    fk_def += " }),"
                    foreign_keys.append(fk_def)
                """            

            # Create table definition
            table_def = f"export const {table_name} = pgTable('{table_name}', {{\n"
            table_def += "\n".join(columns)
            if foreign_keys:
                table_def += "\n" + "\n".join(foreign_keys)
            table_def += "\n});\n"
            
            table_definitions.append(table_def)
            exports.append(table_name)
    
    # Create exports statement
    # not needed, redudant: export_statement = f"export {{ {', '.join(exports)} }};\n"
    
    # Combine all code
    return drizzle_imports + "\n".join(table_definitions) + "\n"# + export_statement

def save_to_file(content, output_file):
    """Save generated Drizzle schema to a file."""
    try:
        with open(output_file, 'w') as f:
            f.write(content)
        print(f"Drizzle schema successfully written to {output_file}")
    except Exception as e:
        sys.exit(f"Error writing to output file: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate Drizzle ORM schema from XML definition.')
    parser.add_argument('input_file', help='Path to the input XML schema file')
    parser.add_argument('-o', '--output', required=True, help='Path to the output Drizzle schema file')
    
    args = parser.parse_args()
    
    drizzle_schema = generate_drizzle_schema(args.input_file)
    
    # Save to specified output file
    save_to_file(drizzle_schema, args.output)
