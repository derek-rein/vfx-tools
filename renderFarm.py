import os
import tempfile
import json
import time
import modal
from pathlib import Path
from datetime import datetime

# Import bpy in a way that works both inside and outside Blender
try:
    import bpy
    from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty, FloatProperty
    from bpy.types import Operator, Panel
    INSIDE_BLENDER = True
except ImportError:
    INSIDE_BLENDER = False
    # Mock classes for development outside Blender
    class StringProperty:
        def __init__(self, **kwargs): pass
    class IntProperty:
        def __init__(self, **kwargs): pass
    class BoolProperty:
        def __init__(self, **kwargs): pass
    class EnumProperty:
        def __init__(self, **kwargs): pass
    class FloatProperty:
        def __init__(self, **kwargs): pass
    class Operator:
        pass
    class Panel:
        pass

bl_info = {
    "name": "Modal VFX Render Farm",
    "author": "Your Name",
    "version": (1, 0),
    "blender": (4, 3, 0),
    "location": "Properties > Render > Modal VFX Render Farm",
    "description": "Professional VFX render farm using Modal's cloud infrastructure",
    "category": "Render",
}

# Modal app definition
app = modal.App("blender-vfx-render-farm")

# Create a persistent volume to store assets and renders
assets_volume = modal.Volume.from_name("blender-assets-volume", create_if_missing=True)
render_volume = modal.Volume.from_name("blender-renders-volume", create_if_missing=True)

# Container images - using Blender 4.3 compatible version
rendering_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("xorg", "libxkbcommon0")  # X11 dependencies
    .pip_install("bpy==4.3.0")  # Updated to Blender 4.3
)

# Database to track file changes
class AssetTracker:
    def __init__(self, db_path):
        import sqlite3
        import os
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        
        # Create table if it doesn't exist
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            path TEXT PRIMARY KEY,
            hash TEXT,
            last_modified REAL
        )
        ''')
        self.conn.commit()
    
    def get_asset_hash(self, path):
        self.cursor.execute("SELECT hash FROM assets WHERE path = ?", (path,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def update_asset(self, path, file_hash, modified_time):
        self.cursor.execute(
            "INSERT OR REPLACE INTO assets (path, hash, last_modified) VALUES (?, ?, ?)",
            (path, file_hash, modified_time)
        )
        self.conn.commit()
    
    def close(self):
        self.conn.close()

def calculate_file_hash(file_path):
    """Calculate MD5 hash of a file."""
    import hashlib
    
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def upload_to_dropbox(file_path, dropbox_path):
    """Upload a file to Dropbox."""
    import dropbox
    import os
    
    # Get Dropbox API token from environment
    api_token = os.environ.get("DROPBOX_API_TOKEN")
    if not api_token:
        print("Warning: DROPBOX_API_TOKEN not set, skipping Dropbox upload")
        return False
    
    try:
        dbx = dropbox.Dropbox(api_token)
        
        # Read file
        with open(file_path, "rb") as f:
            file_data = f.read()
        
        # Upload file
        dbx.files_upload(
            file_data,
            dropbox_path,
            mode=dropbox.files.WriteMode.overwrite
        )
        
        print(f"Uploaded {file_path} to Dropbox at {dropbox_path}")
        return True
    except Exception as e:
        print(f"Error uploading to Dropbox: {str(e)}")
        return False

@app.function(
    gpu="L40S",
    image=rendering_image,
    volumes={
        "/assets": assets_volume,
        "/renders": render_volume
    }
)
def render_frame(blend_file_path: str, frame_number: int = 0) -> dict:
    """Renders a single frame of a Blender file using compositor settings."""
    import bpy
    import os
    from pathlib import Path

    # The blend file is now on the volume
    input_path = blend_file_path
    temp_output_dir = "/renders/temp"
    os.makedirs(temp_output_dir, exist_ok=True)

    # Open the blend file
    bpy.ops.wm.open_mainfile(filepath=input_path)
    
    # Set the frame
    bpy.context.scene.frame_set(frame_number)
    
    # Configure GPU rendering
    configure_rendering(bpy.context, with_gpu=True)
    
    # Store original output paths from File Output nodes
    original_paths = {}
    output_nodes = get_file_output_nodes()
    
    # Temporarily redirect output to our temp directory
    for node_index, node in enumerate(output_nodes):
        original_paths[node_index] = {
            'base_path': node.base_path,
            'file_slots': []
        }
        
        # Store original file slots settings
        for slot_index, slot in enumerate(node.file_slots):
            original_paths[node_index]['file_slots'].append({
                'path': slot.path,
                'format': {
                    'file_format': slot.format.file_format,
                    'color_depth': getattr(slot.format, 'color_depth', None),
                    'exr_codec': getattr(slot.format, 'exr_codec', None),
                    'color_mode': getattr(slot.format, 'color_mode', None),
                }
            })
        
        # Redirect output to temp directory
        node.base_path = temp_output_dir
    
    # Render
    bpy.ops.render.render(write_still=True)
    
    # Collect all rendered files
    rendered_files = {}
    for root, dirs, files in os.walk(temp_output_dir):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, temp_output_dir)
            rendered_files[rel_path] = file_path  # Store the path instead of bytes
    
    # Restore original output paths
    for node_index, node in enumerate(output_nodes):
        if node_index in original_paths:
            node.base_path = original_paths[node_index]['base_path']
            for slot_index, slot in enumerate(node.file_slots):
                if slot_index < len(original_paths[node_index]['file_slots']):
                    slot.path = original_paths[node_index]['file_slots'][slot_index]['path']
    
    # Return all rendered files with their relative paths
    return {
        'frame': frame_number,
        'files': rendered_files,
        'original_paths': original_paths
    }

@app.function(
    image=rendering_image,
    volumes={
        "/assets": assets_volume,
        "/renders": render_volume
    }
)
def prepare_blend_file(blend_file_path: str) -> str:
    """Unpacks and prepares a blend file for rendering, returning the path on the volume."""
    import bpy
    import os
    from pathlib import Path
    
    # Create a temporary directory for the blend file
    os.makedirs("/assets/blend_files", exist_ok=True)
    
    # Generate a unique name based on the original filename
    filename = os.path.basename(blend_file_path)
    volume_blend_path = f"/assets/blend_files/{filename}"
    
    # Copy the blend file to the volume
    Path(volume_blend_path).write_bytes(Path(blend_file_path).read_bytes())
    
    # Open the blend file
    bpy.ops.wm.open_mainfile(filepath=volume_blend_path)
    
    # Unpack all packed files
    bpy.ops.file.unpack_all(method='USE_LOCAL')
    
    # Save the file with unpacked assets
    bpy.ops.wm.save_as_mainfile(filepath=volume_blend_path)
    
    return volume_blend_path

def get_file_output_nodes():
    """Get all File Output nodes from the compositor."""
    output_nodes = []
    
    for scene in bpy.data.scenes:
        if scene.node_tree and scene.use_nodes:
            for node in scene.node_tree.nodes:
                if node.type == 'OUTPUT_FILE':
                    output_nodes.append(node)
    
    return output_nodes

def configure_rendering(ctx, with_gpu: bool):
    """Configure rendering settings."""
    # Set render engine to Cycles
    ctx.scene.render.engine = "CYCLES"
    
    # Use GPU acceleration if available
    if with_gpu:
        # Updated for Blender 4.3 API
        ctx.preferences.addons["cycles"].preferences.compute_device_type = "CUDA"
        ctx.scene.cycles.device = "GPU"

        # Reload devices to update configuration
        ctx.preferences.addons["cycles"].preferences.get_devices()
        for device in ctx.preferences.addons["cycles"].preferences.devices:
            device.use = True
    else:
        ctx.scene.cycles.device = "CPU"

    # Report rendering devices for debugging
    if "cycles" in ctx.preferences.addons:
        for dev in ctx.preferences.addons["cycles"].preferences.devices:
            print(f"ID:{dev.id} Name:{dev.name} Type:{dev.type} Use:{dev.use}")

class ModalVFXRenderFarmOperator(Operator):
    """Render using Modal's cloud infrastructure for VFX production"""
    bl_idname = "render.modal_vfx_render_farm"
    bl_label = "Render on Modal"
    bl_description = "Send your VFX scene to Modal's cloud for rendering using compositor settings"
    
    render_type = EnumProperty(
        name="Render Type",
        items=[
            ('FRAME', "Single Frame", "Render the current frame"),
            ('ANIMATION', "Animation", "Render the full animation"),
            ('RANGE', "Frame Range", "Render a specific range of frames")
        ],
        default='FRAME'
    )
    
    start_frame = IntProperty(
        name="Start Frame",
        default=1,
        min=1
    )
    
    end_frame = IntProperty(
        name="End Frame",
        default=250,
        min=1
    )
    
    gpu_enabled = BoolProperty(
        name="Use GPU",
        default=True,
        description="Use GPU acceleration for rendering"
    )
    
    max_containers = IntProperty(
        name="Max Containers",
        default=10,
        min=1,
        max=100,
        description="Maximum number of containers to use for rendering"
    )
    
    upload_to_dropbox = BoolProperty(
        name="Upload to Dropbox",
        default=False,
        description="Upload rendered files to Dropbox"
    )
    
    dropbox_folder = StringProperty(
        name="Dropbox Folder",
        default="/Renders",
        description="Folder in Dropbox to upload renders to"
    )

    def execute(self, context):
        # Check if there are any File Output nodes in the compositor
        output_nodes = get_file_output_nodes()
        if not output_nodes:
            self.report({'ERROR'}, "No File Output nodes found in the compositor. Please set up your output in the compositor first.")
            return {'CANCELLED'}
        
        # Save the current blend file to the submitter folder
        import os
        import time
        from pathlib import Path
        
        # Create submitter directory if it doesn't exist
        submitter_dir = "/Users/derek/Skunkworks Dropbox/studio/submitter"
        os.makedirs(submitter_dir, exist_ok=True)
        
        # Create a unique filename based on the current file
        current_file = bpy.data.filepath
        if not current_file:
            self.report({'ERROR'}, "Please save your file before rendering.")
            return {'CANCELLED'}
            
        filename = os.path.basename(current_file)
        timestamp = int(time.time())
        temp_blend = os.path.join(submitter_dir, f"{os.path.splitext(filename)[0]}_{timestamp}.blend")
        
        # Save current file
        bpy.ops.wm.save_as_mainfile(filepath=temp_blend, copy=True)
        
        # Initialize asset tracker
        db_path = os.path.join(submitter_dir, "asset_tracker.db")
        tracker = AssetTracker(db_path)
        
        self.report({'INFO'}, "Starting render on Modal. Using File Output nodes from compositor.")
        
        try:
            # Initialize Modal client
            with modal.Session() as session:
                # Prepare the blend file on the volume
                volume_blend_path = session.app.prepare_blend_file.remote(temp_blend)
                
                if self.render_type == 'FRAME':
                    # Render single frame
                    frame = context.scene.frame_current
                    result = session.app.render_frame.remote(
                        volume_blend_path, 
                        frame
                    )
                    
                    # Save the rendered files
                    self._process_rendered_files(result, self.upload_to_dropbox, self.dropbox_folder)
                    
                    self.report({'INFO'}, f"Rendered frame {frame} saved to original output paths")
                    
                else:
                    # Determine frame range
                    if self.render_type == 'ANIMATION':
                        start = context.scene.frame_start
                        end = context.scene.frame_end
                    else:  # RANGE
                        start = self.start_frame
                        end = self.end_frame
                    
                    # Create frame arguments for parallel rendering
                    args = [(volume_blend_path, frame) for frame in range(start, end + 1)]
                    
                    # Render frames in parallel
                    results = list(session.app.render_frame.map(args, max_concurrency=self.max_containers))
                    
                    # Save all rendered files
                    for result in results:
                        self._process_rendered_files(result, self.upload_to_dropbox, self.dropbox_folder)
                    
                    self.report({'INFO'}, f"All frames rendered and saved to original output paths")
            
            # Close the asset tracker
            tracker.close()
            
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Error rendering on Modal: {str(e)}")
            # Close the asset tracker
            tracker.close()
            return {'CANCELLED'}
    
    def _process_rendered_files(self, result, upload_to_dropbox=False, dropbox_folder="/Renders"):
        """Process rendered files - download from volume and optionally upload to Dropbox."""
        import os
        from pathlib import Path
        
        frame = result['frame']
        files = result['files']
        original_paths = result['original_paths']
        
        # Process each file
        for rel_path, file_path in files.items():
            # Determine the original output path
            original_path = None
            
            # Try to match the file to its original output node and slot
            for node_index, node_info in original_paths.items():
                base_path = node_info['base_path']
                
                for slot_info in node_info['file_slots']:
                    # Replace frame number placeholder with actual frame number
                    slot_path = slot_info['path'].replace('#', f"{frame:04d}")
                    
                    # Construct potential original path
                    potential_path = os.path.join(base_path, slot_path)
                    
                    # Check if this is the right path
                    if os.path.basename(rel_path) == os.path.basename(slot_path):
                        original_path = potential_path
                        break
                
                if original_path:
                    break
            
            # If we couldn't determine the original path, use a fallback
            if not original_path:
                # Use the relative path directly with the first node's base path
                if original_paths and 0 in original_paths:
                    original_path = os.path.join(original_paths[0]['base_path'], rel_path)
                else:
                    # Last resort fallback
                    fallback_dir = os.path.join(bpy.path.abspath("//"), "modal_render_output")
                    original_path = os.path.join(fallback_dir, rel_path)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(original_path), exist_ok=True)
            
            # Copy the file from the volume to the local path
            Path(original_path).write_bytes(Path(file_path).read_bytes())
            print(f"Saved file to {original_path}")
            
            # Upload to Dropbox if requested
            if upload_to_dropbox:
                dropbox_path = os.path.join(dropbox_folder, os.path.basename(original_path))
                upload_to_dropbox(original_path, dropbox_path)

class ModalVFXRenderFarmPanel(Panel):
    """Panel for Modal VFX Render Farm settings"""
    bl_label = "Modal VFX Render Farm"
    bl_idname = "RENDER_PT_modal_vfx_render_farm"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"
    
    def draw(self, context):
        layout = self.layout
        
        # Modal credentials check
        try:
            modal.config.get_current_config()
            has_credentials = True
        except:
            has_credentials = False
        
        if not has_credentials:
            layout.label(text="Modal credentials not found")
            layout.label(text="Run 'modal setup' in your terminal")
            layout.operator("wm.url_open", text="Sign up for Modal").url = "https://modal.com/"
            return
        
        # Check if there are any File Output nodes
        output_nodes = get_file_output_nodes()
        if not output_nodes:
            box = layout.box()
            box.label(text="No File Output nodes found in compositor", icon='ERROR')
            box.label(text="Please set up File Output nodes in the compositor")
            box.operator("wm.url_open", text="Learn about Compositor File Output").url = "https://docs.blender.org/manual/en/latest/compositing/types/output/file.html"
            return
        
        # Display File Output nodes information
        box = layout.box()
        box.label(text="Compositor Output Nodes:", icon='NODE_COMPOSITING')
        
        for i, node in enumerate(output_nodes):
            node_box = box.box()
            node_box.label(text=f"Node: {node.name}")
            node_box.label(text=f"Base Path: {node.base_path}")
            
            for j, slot in enumerate(node.file_slots):
                slot_row = node_box.row()
                slot_row.label(text=f"Slot {j+1}: {slot.path}")
                format_info = f"{slot.format.file_format}"
                if hasattr(slot.format, 'color_depth'):
                    format_info += f", {slot.format.color_depth}-bit"
                slot_row.label(text=format_info)
        
        # Render settings
        layout.label(text="Render Settings:")
        
        # Operator properties
        op_props = layout.operator("render.modal_vfx_render_farm")
        
        # Render type selection
        layout.prop(op_props, "render_type")
        
        # Frame range (only show if animation or range is selected)
        if op_props.render_type != 'FRAME':
            row = layout.row()
            row.prop(op_props, "start_frame")
            row.prop(op_props, "end_frame")
        
        # Hardware options
        layout.label(text="Hardware Settings:")
        layout.prop(op_props, "gpu_enabled")
        layout.prop(op_props, "max_containers")
        
        # Dropbox options
        layout.label(text="Output Settings:")
        layout.prop(op_props, "upload_to_dropbox")
        if op_props.upload_to_dropbox:
            layout.prop(op_props, "dropbox_folder")

def register():
    bpy.utils.register_class(ModalVFXRenderFarmOperator)
    bpy.utils.register_class(ModalVFXRenderFarmPanel)

def unregister():
    bpy.utils.unregister_class(ModalVFXRenderFarmPanel)
    bpy.utils.unregister_class(ModalVFXRenderFarmOperator)

if __name__ == "__main__":
    register()
