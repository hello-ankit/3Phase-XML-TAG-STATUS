import os
import json
import pandas as pd
import xml.etree.ElementTree as ET
from flask import Flask, request, jsonify, Response, send_from_directory, render_template
from werkzeug.utils import secure_filename
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Use the same path as XML_ALL_TAGS.py
UPLOAD_FOLDER = r"C:\Users\41015078\Desktop\3Phase XML Tag Status FrontEnd"
ALLOWED_EXTENSIONS = {'xml'}

# Create upload folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# List of tags to check (same as XML_ALL_TAGS.py)
TAGS_TO_CHECK = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D1251', 'D1300']

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_xml_file(xml_file):
    try:
        # Parse XML file
        tree = ET.parse(xml_file)
        root = tree.getroot()
        
        # Dictionary to store results
        result = {
            'file': os.path.basename(xml_file),
            'meter': 'N/A',
            'tag_status': {},
            'missing_tags': [],
            'is_faulty': False
        }
        
        # Check for G1 meter number
        has_meter = False
        for G1 in root.iter('G1'):
            if G1.text and G1.text.strip():
                result['meter'] = G1.text.strip()
                has_meter = True
                break
        
        # If no meter number found, mark as faulty and return immediately
        if not has_meter:
            result['is_faulty'] = True
            logger.info(f"File marked as faulty - No meter number found: {result['file']}")
            return result
            
        # Only check tags if we have a meter number
        for tag in TAGS_TO_CHECK:
            if root.find(f".//{tag}") is not None:
                result['tag_status'][tag] = 'yes'
            else:
                result['tag_status'][tag] = 'no'
                result['missing_tags'].append(tag)
        
        # If we have a meter number, file is not faulty
        result['is_faulty'] = False
        logger.info(f"File marked as normal - Has meter number: {result['file']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing file {xml_file}: {str(e)}")
        # If there's any error in parsing, mark as faulty
        return {
            'file': os.path.basename(xml_file),
            'meter': 'N/A',
            'is_faulty': True,
            'tag_status': {},
            'missing_tags': []
        }

def create_visualization(results):
    # Count of faulty vs non-faulty files
    faulty_count = sum(1 for r in results if r['is_faulty'])
    normal_count = len(results) - faulty_count
    
    # Create bar chart data for tag presence
    tag_stats = {tag: {'present': 0, 'missing': 0} for tag in TAGS_TO_CHECK}
    for result in results:
        for tag, status in result['tag_status'].items():
            if status == 'yes':
                tag_stats[tag]['present'] += 1
            else:
                tag_stats[tag]['missing'] += 1

    # Create the visualization data
    visualization_data = {
        'charts': {
            'faultyChart': {
                'type': 'doughnut',
                'data': {
                    'labels': ['Normal Files', 'Faulty Files'],
                    'datasets': [{
                        'data': [normal_count, faulty_count],
                        'backgroundColor': ['#4CAF50', '#C8A2C8'],
                        'borderColor': ['#388E3C', '#d32f2f'],
                        'borderWidth': 1
                    }]
                }
            },
            'exportChart': {
                'type': 'bar',
                'data': {
                    'labels': ['Total Files', 'Faulty Files', 'Normal Files'],
                    'datasets': [{
                        'data': [len(results), faulty_count, normal_count],
                        'backgroundColor': ['#2196F3', '#C8A2C8', '#4CAF50'],
                        'borderColor': ['#1976D2', '#d32f2f', '#388E3C'],
                        'borderWidth': 1
                    }]
                }
            }
        }
    }
    
    return json.dumps(visualization_data)

# Store the results globally
global_results = {
    'results': [],
    'faulty_files': []
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/export-faulty', methods=['POST'])
def export_faulty():
    try:
        # Get export directory from request
        export_dir = request.json.get('export_dir', UPLOAD_FOLDER)
        
        # Create directory if it doesn't exist
        if not os.path.exists(export_dir):
            os.makedirs(export_dir, exist_ok=True)
        
        # Get faulty files from global results
        faulty_files = [result for result in global_results['results'] if result['is_faulty']]
        
        if not faulty_files:
            return jsonify({"error": "No faulty files to export"}), 400
            
        # Create DataFrame with faulty files information
        export_data = []
        for file in faulty_files:
            export_data.append({
                'File': file['file'],
                'Meter': file['meter'],
                'Status': 'Faulty',
                'Missing Tags': ', '.join(file['missing_tags']) if file.get('missing_tags') else 'N/A'
            })
            
        # Create DataFrame and export to CSV
        df = pd.DataFrame(export_data)
        csv_path = os.path.join(export_dir, 'faulty_xml_files.csv')
        df.to_csv(csv_path, index=False)
        
        logger.info(f"Successfully exported {len(faulty_files)} faulty files to {csv_path}")
        return jsonify({
            "message": f"Successfully exported {len(faulty_files)} faulty files",
            "file_path": csv_path
        })
        
    except Exception as e:
        logger.error(f"Error exporting faulty files: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/export-normal', methods=['POST'])
def export_normal():
    try:
        # Get export directory from request
        export_dir = request.json.get('export_dir', UPLOAD_FOLDER)
        
        # Create directory if it doesn't exist
        if not os.path.exists(export_dir):
            os.makedirs(export_dir, exist_ok=True)
        
        # Get normal (correct) files from global results
        normal_files = [result for result in global_results['results'] if not result['is_faulty']]
        
        if not normal_files:
            return jsonify({"error": "No normal files to export"}), 400
            
        # Create DataFrame with normal files information
        export_data = []
        for file in normal_files:
            # Create a string representation of tag values
            tag_values = ' | '.join([f"{tag}: {file['tag_status'].get(tag, 'N/A')}" 
                                   for tag in TAGS_TO_CHECK])
            
            export_data.append({
                'File': file['file'],
                'Meter': file['meter'],
                'Status': 'Normal',
                'Tag Values': tag_values
            })
            
        # Create DataFrame and export to CSV
        df = pd.DataFrame(export_data)
        csv_path = os.path.join(export_dir, 'normal_xml_files.csv')
        df.to_csv(csv_path, index=False)
        
        logger.info(f"Successfully exported {len(normal_files)} normal files to {csv_path}")
        return jsonify({
            "message": f"Successfully exported {len(normal_files)} normal files",
            "file_path": csv_path
        })
        
    except Exception as e:
        logger.error(f"Error exporting normal files: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/process-xml', methods=['POST'])
def process_xml():
    try:
        if 'files[]' not in request.files:
            logger.error("No files found in request")
            return jsonify({"error": "No files part in the request."}), 400
        
        uploaded_files = request.files.getlist('files[]')
        if not uploaded_files or uploaded_files[0].filename == '':
            logger.error("No files selected")
            return jsonify({"error": "No selected files."}), 400
        
        logger.info(f"Received {len(uploaded_files)} files")
        
        file_paths = []
        for file in uploaded_files:
            if not allowed_file(file.filename):
                logger.error(f"Invalid file type: {file.filename}")
                return jsonify({"error": f"Invalid file type: {file.filename}"}), 400
            
            filename = secure_filename(file.filename)
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(file_path)
            file_paths.append(file_path)
            logger.info(f"Saved file: {file_path}")
        
        def generate():
            results = []
            total_files = len(file_paths)
            processed = 0
            faulty_count = 0
            
            # Process files with ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as executor:
                future_to_file = {executor.submit(process_xml_file, file_path): file_path 
                                for file_path in file_paths}
                
                for future in as_completed(future_to_file):
                    file_path = future_to_file[future]
                    processed += 1
                    progress = (processed / total_files) * 100
                    
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                            if result['is_faulty']:
                                faulty_count += 1
                                logger.info(f"File marked as faulty: {result['file']} (Total faulty: {faulty_count})")
                            else:
                                logger.info(f"File marked as normal: {result['file']}")
                        
                        # Send progress update with faulty count
                            progress_data = {
                                        "progress": round(progress, 2),
                                        "current_file": os.path.basename(file_path),
                                        "faulty_count": faulty_count
                                    }
                            
                            yield f"data: {json.dumps(progress_data)}\n\n"
                        
                    except Exception as e:
                        logger.error(f"Error processing {file_path}: {str(e)}")
                        error_data = {
                            "progress": round(progress, 2),
                            "current_file": os.path.basename(file_path),
                            "error": str(e)
                        }
                        yield f"data: {json.dumps(error_data)}\n\n"
            
            # Store results globally for export
            global_results['results'] = results
            
            # Create visualization data
            visualization_data = create_visualization(results)
            
            # Send completion message with visualization data
            # Build the table data separately for clarity
            table_data = []
            for r in results:
                table_data.append({
                    "File": r["file"],
                    "Meter": r["meter"],
                    "Status": "Faulty" if r["is_faulty"] else "Normal",
                    "Missing Tags": ", ".join(r["missing_tags"]) if r["missing_tags"] else "None",
                    "Tag Values": (
                        " | ".join([f"{tag}: {r['tag_status'].get(tag, 'N/A')}" for tag in TAGS_TO_CHECK])
                        if not r["is_faulty"] else "N/A"
                    )
                })
            
            # Structure the completion data
            completion_data = {
                "complete": True,
                "total_processed": len(results),
                "faulty_files": faulty_count,
                "visualization": visualization_data,
                "table_data": table_data
            }
            
            # Final yield
            yield f"data: {json.dumps(completion_data)}\n\n"
            
            
        return Response(generate(), mimetype='text/event-stream')
        
    except Exception as e:
        logger.error(f"Error in process_xml: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/get-export-directories', methods=['GET'])
def get_export_directories():
    """Get list of directories for export"""
    try:
        # Get base directories
        base_dirs = [
            UPLOAD_FOLDER,
            os.path.join(os.path.expanduser('~'), 'Documents'),
            os.path.join(os.path.expanduser('~'), 'Desktop')
        ]
        
        return jsonify({"directories": base_dirs})
    except Exception as e:
        logger.error(f"Error getting export directories: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True,host='0.0.0.0',port=5004)
