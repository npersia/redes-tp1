from mininet.topo import Topo
from mininet.link import TCLink


class Topologia5Host3Switch(Topo):
    """
    h1, h2 -- s1 -- s2 -- s3 -- h5 (SERVER)
    h3, h4 -- s2

    Los parametros de red (perdida y delay) se aplican SOLO en el enlace
    del servidor (h5 -- s3). Asi, todo cliente ve exactamente las mismas
    condiciones contra el server, sin importar por que switches pase:
      - perdida: 'perdida' % en cada sentido
      - delay:   'delay' en cada sentido  ->  RTT = 2 * delay
    """

    def __init__(self, delay='20ms', perdida=10, mtu=1500):
        Topo.__init__(self)

        perdida = float(perdida)

        # Switches
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Hosts
        h1 = self.addHost('h1')
        h2 = self.addHost('h2')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h5 = self.addHost('h5')  # SERVER

        # Clientes: enlaces sin impedimentos
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s2)
        self.addLink(h4, s2)

        # Enlaces entre switches: sin impedimentos
        self.addLink(s1, s2)
        self.addLink(s2, s3)

        # Servidor: aca van la perdida y el delay
        self.addLink(
            h5, s3,
            cls=TCLink,
            loss=perdida,
            delay=delay,
            mtu=mtu
        )


topos = {
    'Topologia5Host3Switch': Topologia5Host3Switch
}