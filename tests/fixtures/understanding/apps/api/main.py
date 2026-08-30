from fastapi import FastAPI

from orders import OrderService

app = FastAPI()


@app.get("/login")
def login():
    return {"ok": True}


@app.post("/orders")
def create_order():
    return OrderService().create()
