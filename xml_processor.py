import os
import json
import pandas as pd
import xml.etree.ElementTree as ET
import multiprocessing
import traceback
from flask import Flask, request, jsonify, Response, send_from_directory, render_template
from werkzeug.utils import secure_filename
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
import io
import lxml.etree as lxml_etree  # Using lxml for faster parsing

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Use the same path as XML_ALL_TAGS.py
UPLOAD_FOLDER = r"E:\3Phase XML Tag Status FrontEnd"
ALLOWED_EXTENSIONS = {'xml', 'csv'}
TAGS_TO_CHECK = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D1251', 'D1300']

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    logger.info(f"Created uploads directory at {os.path.abspath(UPLOAD_FOLDER)}")
else:
    logger.info(f"Using existing uploads directory at {os.path.abspath(UPLOAD_FOLDER)}")

# Add root route to serve index.html
@app.route('/')
def index():
    return render_template('index.html')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_xml_file(file_path):
    try:
        # Use lxml for faster parsing
        parser = lxml_etree.XMLParser(recover=True)  # Enable recovery mode for malformed XML
        tree = lxml_etree.parse(file_path, parser=parser)
        root = tree.getroot()
        
        # Pre-compute tag presence using lxml's faster find method
        tag_presence = {}
        missing_tags = []
        
        for tag in TAGS_TO_CHECK:
            tag_found = root.find(f".//{tag}") is not None
            tag_presence[tag] = tag_found
            if not tag_found:
                missing_tags.append(tag)
        
        # Get meter value
        meter_value = root.find(".//METER")
        meter = meter_value.text.strip() if meter_value is not None and meter_value.text else "N/A"
        
        # Check if any required tags are missing
        is_faulty = len(missing_tags) > 0  # If any tags are missing, it's faulty
        
        if is_faulty:
            logger.info(f"File {file_path} is faulty. Missing tags: {missing_tags}")
        
        return {
            "file": os.path.basename(file_path),
            "meter": meter,
            "missing_tags": missing_tags,
            **tag_presence
        }, file_path if is_faulty else None
    except Exception as e:
        logger.error(f"Error processing file {file_path}: {str(e)}")
        return None, file_path  # If there's an error, mark as faulty

def process_files_parallel(file_paths):
    results = []
    faulty_files = []
    total_files = len(file_paths)
    processed_files = 0
    
    # Use ProcessPoolExecutor for CPU-bound operations
    with multiprocessing.Pool(processes=min(multiprocessing.cpu_count() * 2, 8)) as pool:
        # Process files in parallel with imap for better memory usage
        for result, faulty in pool.imap_unordered(process_xml_file, file_paths):
            processed_files += 1
            if result:
                results.append(result)
            if faulty:
                faulty_files.append(faulty)
            
            # Calculate progress
            progress = (processed_files / total_files) * 100
            
            # Yield progress update with current results
            yield {
                'progress': progress,
                'current_file': os.path.basename(faulty if faulty else result['file']),
                'processed_files': processed_files,
                'total_files': total_files,
                'results': results,
                'faulty_files': faulty_files
            }
    
    return results, faulty_files

@app.route('/api/process-xml', methods=['POST'])
def process_xml():
    try:
        if 'files' not in request.files:
            return jsonify({"error": "No files part in the request."}), 400
        
        files = request.files.getlist('files')
        if not files or files[0].filename == '':
            return jsonify({"error": "No selected files."}), 400
        
        # Log the received files
        logger.info(f"Received files: {[f.filename for f in files]}")
        
        # Check if it's a CSV file
        if files[0].filename.lower().endswith('.csv'):
            try:
                # Read CSV file
                df = pd.read_csv(files[0])
                path_column = next((col for col in df.columns if 'path' in col.lower() or 'file' in col.lower()), None)
                if not path_column:
                    return jsonify({"error": "No valid file path column in CSV."}), 400
                
                # Get XML file paths from CSV
                xml_paths = df[path_column].dropna().tolist()
                
                # Log the XML paths from CSV
                logger.info(f"XML paths from CSV: {xml_paths}")
                
                # Validate paths exist
                valid_paths = []
                for path in xml_paths:
                    if os.path.exists(path):
                        valid_paths.append(path)
                    else:
                        logger.warning(f"File not found: {path}")
                
                if not valid_paths:
                    return jsonify({"error": "No valid XML files found in CSV."}), 400
                
                files = valid_paths
            except Exception as e:
                return jsonify({"error": f"Error reading CSV file: {str(e)}"}), 400
        else:
            # Validate XML files
            for file in files:
                if not allowed_file(file.filename):
                    return jsonify({"error": f"Invalid file type: {file.filename}. Only XML files are allowed."}), 400
            
            # Save uploaded files temporarily
            temp_files = []
            for file in files:
                filename = secure_filename(file.filename)
                temp_path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(temp_path)
                temp_files.append(temp_path)
                logger.info(f"Saved file to: {temp_path}")
            files = temp_files
        
        def generate_progress():
            try:
                # Process files in parallel
                results = []
                faulty_files = []
                for progress_update in process_files_parallel(files):
                    yield f"data: {json.dumps(progress_update)}\n\n"
                    # Store the last progress update which contains the results
                    if 'results' in progress_update:
                        results = progress_update['results']
                    if 'faulty_files' in progress_update:
                        faulty_files = progress_update['faulty_files']
                
                # Clean up temporary files
                if 'temp_files' in locals():
                    for temp_file in temp_files:
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                
                # Send completion message
                yield f"data: {json.dumps({
                    'complete': True,
                    'total_processed': len(files),
                    'faulty_files': len(faulty_files)
                })}\n\n"
            except Exception as e:
                logger.error(f"Error in generate_progress: {str(e)}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        return Response(generate_progress(), mimetype='text/event-stream')
    except Exception as e:
        logger.error(f"Error in process_xml: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/export-xml-paths', methods=['POST'])
def export_xml_paths():
    try:
        data = request.get_json()
        if not data or 'files' not in data:
            return jsonify({"error": "No files data provided"}), 400
        
        # Generate timestamp for unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Create DataFrame with file paths
        df = pd.DataFrame(data['files'], columns=['XML_File_Paths'])
        
        # Save to CSV
        file_path = os.path.join(UPLOAD_FOLDER, f'xml_paths_{timestamp}.csv')
        df.to_csv(file_path, index=False)
        
        return jsonify({
            "success": True,
            "message": "XML paths exported successfully",
            "file": f'/api/download/results/{timestamp}'
        })
    except Exception as e:
        logger.error(f"Error exporting XML paths: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/results/<timestamp>')
def download_results(timestamp):
    try:
        file_path = os.path.join(UPLOAD_FOLDER, f'xml_paths_{timestamp}.csv')
        if not os.path.exists(file_path):
            return jsonify({"error": "Results file not found"}), 404
        return send_from_directory(UPLOAD_FOLDER, f'xml_paths_{timestamp}.csv')
    except Exception as e:
        logger.error(f"Error downloading results: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    logger.info("Starting XML Processor application")
    app.run(debug=True)
