from mininet.topo import Topo
from mininet.link import TCLink


class ToplogiaBasicon(Topo):

    def __init__(self):
        Topo.__init__(self)

        mtuBase = 1500
        delayBase = '20ms'
        perdidaBase = 10

        h1 = self.addHost('h1')
        h2 = self.addHost('h2')

        self.addLink(
            h1, h2,
            cls=TCLink,
            loss=perdidaBase,
            delay=delayBase,
            mtu=mtuBase
        )


topos = {
    'TopologiaBasicon': lambda: ToplogiaBasicon()
}
