from fastapi import APIRouter, Request

router = APIRouter(prefix="/auto-announce", tags=["auto-announce"])

@router.get("/health")
async def health_check(request: Request):
    return {"status": "healthy"}



@router.get("/gpt")
async def gpt_complition(request: Request, msg: str):
    gpt = await request.app.state.gpt.format_email(
        email_body=msg,
        template="ответь",
        prompt="привет",
    )

    print(msg)
    return {"responce": gpt}
