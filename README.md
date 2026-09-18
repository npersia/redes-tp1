# redes-tp1
tp1 de redes

Como se ejecutan las redes de prueba:
    *Topologia basica con 2 host y 3 routers:
    ```text
    sudo mn --custom src/topologias/topologiaBasica.py --topo TopologiaBasica
    ```
    *Topologia con trafico simulado (todavia no tiene trafico):
    ```text
    sudo mn --custom src/topologias/topologiaTrafico.py --topo TopologiaTrafico
    ```
