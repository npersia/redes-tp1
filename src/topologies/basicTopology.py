from mininet.topo import Topo
from mininet.link import TCLink


class TopologiaBasica(Topo):
    "Caso 1 de topologia basica"
    "- 2 hosts y 2 caminos posibles entre ellos"
    "- imagen topologia: ./topologiaBasica.png"

    def __init__(self):
        Topo.__init__(self)

        mtuBase = 1500
        delayBase = '5ms'
        perdidaBase = 10

        h1 = self.addHost('h1')
        h2 = self.addHost('h2')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        self.addLink(
            h1, s1,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )

        self.addLink(
            h2, s1,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )

        self.addLink(
            h1, s2,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )

        self.addLink(
            s2, s3,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )

        self.addLink(
            s3, h2,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )


topos = {
    'TopologiaBasica': lambda: TopologiaBasica()
}
