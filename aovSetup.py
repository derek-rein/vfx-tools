"""
AOV Setup Helper Addon for Blender

This addon sets up a complete VFX AOV (Arbitrary Output Variable) system with:
- Main beauty and lighting passes (16-bit)
- Data passes (32-bit)
- Cryptomatte passes (32-bit)

Usage:
1. Install the addon in Blender (Edit > Preferences > Add-ons > Install)
2. Enable the addon in the add-ons list
3. Go to View Layer Properties (in the Properties Editor)
4. Find the "VFX AOV Setup" panel
5. Click "Setup VFX AOVs" button

The addon will:
- Switch to Cycles render engine (required for Cryptomatte)
- Enable all necessary passes in the view layer
- Set up the compositor with three output nodes:
  * main_*.exr: Beauty and lighting passes (16-bit)
  * data_*.exr: Data passes (normal, depth, position, motion)
  * crypto_*.exr: Cryptomatte passes (object, material, asset)

Output Structure:
main_*.exr:
  - beauty (combined)
  - diffuseDirect
  - diffuseIndirect
  - diffuseColor
  - specularDirect
  - specularIndirect
  - specularColor
  - transmissionDirect
  - transmissionIndirect
  - transmissionColor
  - emission
  - background
  - shadow
  - ao

data_*.exr:
  - normal
  - depth
  - position
  - motion

crypto_*.exr:
  - CryptoObject, CryptoObject00-02
  - CryptoMaterial, CryptoMaterial00-02
  - CryptoAsset, CryptoAsset00-02

Note: All output files will be saved in a "renders" folder relative to your blend file.
"""

bl_info = {
    "name": "AOV Setup Helper",
    "author": "Derek Rein",
    "version": (1, 1),
    "blender": (4, 0, 0),
    "location": "View Layer Properties > AOV Setup Panel",
    "description": "Sets up Main (16-bit) and Data/Cryptomatte (32-bit) AOVs with VFX naming",
    "category": "Render",
}

import bpy
from bpy.types import Operator, Panel

class AOVSETUP_OT_setup_aovs(Operator):
    """Set up Main (16-bit) and Data/Cryptomatte (32-bit) AOVs with VFX naming"""
    bl_idname = "aovsetup.setup_aovs"
    bl_label = "Setup VFX AOVs"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Ensure Cycles is active (required for Cryptomatte)
        if context.scene.render.engine != 'CYCLES':
            context.scene.render.engine = 'CYCLES'
            self.report({'INFO'}, "Switched to Cycles for Cryptomatte support")

        # Get the current view layer
        view_layer = context.view_layer

        # --- Main AOVs (Beauty and Lighting Passes) ---
        view_layer.use_pass_combined = True  # beauty
        view_layer.use_pass_diffuse_direct = True   # diffuseDirect
        view_layer.use_pass_diffuse_indirect = True # diffuseIndirect
        view_layer.use_pass_diffuse_color = True    # diffuseColor
        view_layer.use_pass_glossy_direct = True    # specularDirect
        view_layer.use_pass_glossy_indirect = True  # specularIndirect
        view_layer.use_pass_glossy_color = True     # specularColor
        view_layer.use_pass_transmission_direct = True    # transmissionDirect
        view_layer.use_pass_transmission_indirect = True  # transmissionIndirect
        view_layer.use_pass_transmission_color = True     # transmissionColor
        view_layer.use_pass_emit = True  # emission
        view_layer.use_pass_environment = True  # background
        view_layer.use_pass_shadow = True    # shadow
        view_layer.use_pass_ambient_occlusion = True  # ao
        
        # Enable denoising data passes
        view_layer.cycles.denoising_store_passes = True
        view_layer.cycles.use_denoising = True
        view_layer.cycles.use_pass_denoising_data = True

        # --- Data AOVs (Utility Passes) ---
        view_layer.use_pass_normal = True    # normal
        view_layer.use_pass_z = True         # depth
        view_layer.use_pass_position = True  # position
        view_layer.use_pass_vector = True    # motion

        # --- Cryptomatte AOVs ---
        view_layer.use_pass_cryptomatte_object = True   # cryptoObject
        view_layer.use_pass_cryptomatte_material = True # cryptoMaterial
        view_layer.use_pass_cryptomatte_asset = True    # cryptoAsset

        # --- Compositor Setup ---
        scene = context.scene
        scene.use_nodes = True
        nodes = scene.node_tree.nodes
        links = scene.node_tree.links

        # Clear existing nodes
        nodes.clear()

        # Add Render Layers node
        render_layers = nodes.new(type="CompositorNodeRLayers")
        render_layers.location = (0, 0)
        render_layers.label = "View Layer"

        # --- File Output for Main AOVs (16-bit) ---
        main_output = nodes.new(type="CompositorNodeOutputFile")
        main_output.location = (400, 200)
        main_output.base_path = "//renders/main.####.exr"  # e.g., main_0001.exr
        main_output.format.file_format = 'OPEN_EXR_MULTILAYER'
        main_output.format.color_depth = '16'  # 16-bit float (half)
        main_output.format.color_mode = 'RGBA'
        main_output.label = "Main Passes (16-bit)"

        # Clear default slots and add Main AOVs
        main_output.file_slots.clear()
        main_output.file_slots.new("rgba")
        main_output.file_slots.new("diffuseDirect")
        main_output.file_slots.new("diffuseIndirect")
        main_output.file_slots.new("diffuseColor")
        main_output.file_slots.new("specularDirect")
        main_output.file_slots.new("specularIndirect")
        main_output.file_slots.new("specularColor")
        main_output.file_slots.new("transmissionDirect")
        main_output.file_slots.new("transmissionIndirect")
        main_output.file_slots.new("transmissionColor")
        main_output.file_slots.new("emission")
        main_output.file_slots.new("background")
        main_output.file_slots.new("shadow")
        main_output.file_slots.new("ao")

        # Add Set Alpha node for the main RGBA output
        set_alpha = nodes.new(type="CompositorNodeSetAlpha")
        set_alpha.location = (200, 200)
        set_alpha.label = "Set Alpha for RGBA"
        
        # Connect Image to Set Alpha node
        links.new(render_layers.outputs['Image'], set_alpha.inputs['Image'])
        
        # If Alpha output exists, connect it to the Set Alpha node
        if 'Alpha' in render_layers.outputs:
            links.new(render_layers.outputs['Alpha'], set_alpha.inputs['Alpha'])
        
        # Link Set Alpha output to the rgba input
        links.new(set_alpha.outputs['Image'], main_output.inputs['rgba'])
        
        # Link other Main AOVs
        links.new(render_layers.outputs['DiffDir'], main_output.inputs['diffuseDirect'])
        
        # Add denoise nodes for indirect passes
        denoise_diff_ind = nodes.new(type="CompositorNodeDenoise")
        denoise_diff_ind.location = (200, 100)
        denoise_diff_ind.label = "Denoise Diffuse Indirect"
        
        denoise_gloss_ind = nodes.new(type="CompositorNodeDenoise")
        denoise_gloss_ind.location = (200, 0)
        denoise_gloss_ind.label = "Denoise Specular Indirect"
        
        denoise_trans_ind = nodes.new(type="CompositorNodeDenoise")
        denoise_trans_ind.location = (200, -100)
        denoise_trans_ind.label = "Denoise Transmission Indirect"
        
        # Debug: Print available outputs to help identify the correct names
        output_names = [output.name for output in render_layers.outputs]
        print("Available outputs:", output_names)
        
        # Connect denoising data to all denoise nodes
        denoising_albedo = None
        denoising_normal = None
        
        # Find the correct denoising output names
        for name in output_names:
            if "denoising" in name.lower() and "albedo" in name.lower():
                denoising_albedo = name
            if "denoising" in name.lower() and "normal" in name.lower():
                denoising_normal = name
        
        # Connect denoising data if found
        if denoising_albedo and denoising_normal:
            for denoise_node in [denoise_diff_ind, denoise_gloss_ind, denoise_trans_ind]:
                links.new(render_layers.outputs[denoising_albedo], denoise_node.inputs['Albedo'])
                links.new(render_layers.outputs[denoising_normal], denoise_node.inputs['Normal'])
        
        # Link indirect passes through denoise nodes
        links.new(render_layers.outputs['DiffInd'], denoise_diff_ind.inputs['Image'])
        links.new(denoise_diff_ind.outputs['Image'], main_output.inputs['diffuseIndirect'])
        
        links.new(render_layers.outputs['GlossInd'], denoise_gloss_ind.inputs['Image'])
        links.new(denoise_gloss_ind.outputs['Image'], main_output.inputs['specularIndirect'])
        
        links.new(render_layers.outputs['TransInd'], denoise_trans_ind.inputs['Image'])
        links.new(denoise_trans_ind.outputs['Image'], main_output.inputs['transmissionIndirect'])
        
        # Link direct passes directly
        links.new(render_layers.outputs['GlossDir'], main_output.inputs['specularDirect'])
        links.new(render_layers.outputs['TransDir'], main_output.inputs['transmissionDirect'])
        links.new(render_layers.outputs['Emit'], main_output.inputs['emission'])
        links.new(render_layers.outputs['Env'], main_output.inputs['background'])
        links.new(render_layers.outputs['AO'], main_output.inputs['ao'])
        
        # Link color passes
        links.new(render_layers.outputs['DiffCol'], main_output.inputs['diffuseColor'])
        links.new(render_layers.outputs['GlossCol'], main_output.inputs['specularColor'])
        links.new(render_layers.outputs['TransCol'], main_output.inputs['transmissionColor'])

        # --- File Output for Data AOVs (32-bit) ---
        data_output = nodes.new(type="CompositorNodeOutputFile")
        data_output.location = (400, -200)
        data_output.base_path = "//renders/data.####.exr"  # Use same base name for all outputs
        data_output.format.file_format = 'OPEN_EXR_MULTILAYER'
        data_output.format.color_depth = '32'  # 32-bit float (full)
        data_output.format.color_mode = 'RGBA'
        data_output.label = "Data Passes (32-bit)"

        # Clear default slots and add Data AOVs
        data_output.file_slots.clear()
        data_output.file_slots.new("normal")
        data_output.file_slots.new("depth")
        data_output.file_slots.new("position")
        data_output.file_slots.new("motion")

        # Link Data AOVs
        links.new(render_layers.outputs['Normal'], data_output.inputs['normal'])
        links.new(render_layers.outputs['Depth'], data_output.inputs['depth'])
        links.new(render_layers.outputs['Position'], data_output.inputs['position'])
        links.new(render_layers.outputs['Vector'], data_output.inputs['motion'])

        # --- File Output for Cryptomatte AOVs (32-bit) ---
        crypto_output = nodes.new(type="CompositorNodeOutputFile")
        crypto_output.location = (400, -400)
        crypto_output.base_path = "//renders/crypto.####.exr"  # Use same base name for all outputs
        crypto_output.format.file_format = 'OPEN_EXR_MULTILAYER'
        crypto_output.format.color_depth = '32'  # 32-bit float (full)
        crypto_output.format.color_mode = 'RGBA'
        crypto_output.label = "Cryptomatte Passes (32-bit)"

        # Clear default slots and add Cryptomatte AOVs
        crypto_output.file_slots.clear()
        
        # Add base Cryptomatte slots and numbered passes
        crypto_output.file_slots.new("CryptoObject")
        crypto_output.file_slots.new("CryptoMaterial")
        crypto_output.file_slots.new("CryptoAsset")
        
        # Add numbered passes for each Cryptomatte type
        for i in range(3):
            crypto_output.file_slots.new(f"CryptoObject{i:02d}")
            crypto_output.file_slots.new(f"CryptoMaterial{i:02d}")
            crypto_output.file_slots.new(f"CryptoAsset{i:02d}")

        # Link main image to base Cryptomatte passes
        links.new(render_layers.outputs['Image'], crypto_output.inputs['CryptoObject'])
        links.new(render_layers.outputs['Image'], crypto_output.inputs['CryptoMaterial'])
        links.new(render_layers.outputs['Image'], crypto_output.inputs['CryptoAsset'])

        # Link numbered Cryptomatte passes directly
        for i in range(3):
            links.new(render_layers.outputs[f'CryptoObject{i:02d}'], crypto_output.inputs[f'CryptoObject{i:02d}'])
            links.new(render_layers.outputs[f'CryptoMaterial{i:02d}'], crypto_output.inputs[f'CryptoMaterial{i:02d}'])
            links.new(render_layers.outputs[f'CryptoAsset{i:02d}'], crypto_output.inputs[f'CryptoAsset{i:02d}'])

        print("Available outputs:", [output.name for output in render_layers.outputs])
        self.report({'INFO'}, "VFX AOVs setup complete: Main (16-bit), Data/Crypto (32-bit)")
        return {'FINISHED'}

class VIEWLAYER_PT_aov_setup(Panel):
    """Panel for AOV Setup in View Layer Properties"""
    bl_label = "VFX AOV Setup"
    bl_idname = "VIEWLAYER_PT_aov_setup"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "view_layer"

    def draw(self, context):
        layout = self.layout
        layout.operator("aovsetup.setup_aovs", text="Setup VFX AOVs")

def register():
    bpy.utils.register_class(AOVSETUP_OT_setup_aovs)
    bpy.utils.register_class(VIEWLAYER_PT_aov_setup)

def unregister():
    bpy.utils.unregister_class(AOVSETUP_OT_setup_aovs)
    bpy.utils.unregister_class(VIEWLAYER_PT_aov_setup)

if __name__ == "__main__":
    register()