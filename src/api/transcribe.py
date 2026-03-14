import asyncio
import logging
from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse

from src.services.transcriber_client import TranscriberClient
from src.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/transcribe", summary="Sube un archivo de audio para transcribir")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("es")
):
    """
    Recibe un archivo de audio mediante Form-Data (UploadFile),
    lo convierte asíncronamente a raw PCM Float32 (16kHz, mono) usando FFmpeg,
    y lo envía al microservicio JotaTranscriber vía WebSocket.
    
    Retorna la transcripción final formateada como JSON.
    """
    try:
        audio_data = await file.read()
        
        # Invocamos la herramienta FFmpeg para decodificar al formato crudo requerido
        # pcm_f32le, 16000 hz, 1 canal
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", 
            "-i", "pipe:0",           # Leer de stdin
            "-acodec", "pcm_f32le",   # Float32 Little Endian (requerido por whisper.cpp backend)
            "-ar", "16000",           # 16kHz
            "-ac", "1",               # Mono
            "-f", "f32le",            # Formato de salida puro crudo
            "pipe:1",                 # Escribir en stdout
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        
        stdout_data, _ = await process.communicate(input=audio_data)
        
        if process.returncode != 0:
            logger.error(f"Fallo FFmpeg al procesar el archivo subido '{file.filename}'.")
            raise HTTPException(
                status_code=400, 
                detail="Error procesando o convirtiendo el audio. ¿Es un formato válido compatible con FFmpeg?"
            )
            
        if not stdout_data:
             raise HTTPException(status_code=400, detail="El audio procesado resultó vacío.")
            
        # Instanciamos un cliente de Transcriber único para esta solicitud REST
        client = TranscriberClient(url=settings.TRANSCRIBER_WS_URL, client_id="rest_api")
        
        # Bloqueamos (pseudo-síncronamente gracias al generador del WS) hasta el final
        transcription_text = await client.transcribe_file(stdout_data, language=language)
        
        return JSONResponse(status_code=200, content={
            "filename": file.filename,
            "language": language,
            "text": transcription_text
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Excepción en el endpoint /transcribe: {e}")
        raise HTTPException(status_code=500, detail="Fallo interno en el servicio de transcripción de audio.")
