import torch
from diffusers import AutoPipelineForText2Image
from engine.config import config

class ImageGenerator:
    def __init__(self):
        self.model_id = config.IMAGE_MODEL_NAME
        self.pipeline = None
        
    def load_model(self):
        if self.pipeline is None:
            print(f"Loading Image Model ({self.model_id}) into VRAM...")
            self.pipeline = AutoPipelineForText2Image.from_pretrained(
                self.model_id, torch_dtype=torch.float16, variant="fp16"
            )
            # Enable memory optimizations for lower VRAM
            self.pipeline.enable_model_cpu_offload()
            
    def unload_model(self):
        if self.pipeline is not None:
            print(f"Unloading Image Model ({self.model_id}) from VRAM...")
            del self.pipeline
            torch.cuda.empty_cache()
            self.pipeline = None
            
    def generate_image(self, prompt, context_type="scene"):
        """
        Generates an image based on the prompt. Handles VRAM Fallbacks.
        """
        if config.VRAM_STRATEGY == "A":
            print("VRAM Strategy A: Skipping Image Generation.")
            return None
            
        elif config.VRAM_STRATEGY == "B":
            # Strategy B: We assume LLM was running, so we might need to unload it
            # Currently Ollama manages its own VRAM and drops context if needed,
            # but we force load our image model here, which might contention.
            # Realistically, strategy B relies on Ollama yielding VRAM or us manually
            # stopping the ollama service if it holds it.
            # For this MVP, we will load, generate, and unload to keep footprint small.
            try:
                self.load_model()
            except Exception as e:
                print(f"Image Model load failed: {e}")
                return None

            # SDXL-Turbo generates in 1-4 steps
            image = self.pipeline(prompt=prompt, num_inference_steps=2, guidance_scale=0.0).images[0]

            # Immediately unload
            self.unload_model()

            # Save or Return
            # In a real app we'd save to disk and return path
            return image

        else:
            print(f"Unknown VRAM strategy: {config.VRAM_STRATEGY}. Skipping image generation.")
            return None
