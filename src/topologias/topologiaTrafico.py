from mininet.topo import Topo
from mininet.link import TCLink


class TopologiaTrafico(Topo):
    "Caso 1 de topologia basica"
    "- 2 hosts y 2 caminos posibles entre ellos"
    "- imagen topologia: ./topologiaBasica.png"
    def __init__(self):
        Topo.__init__(self)
        mtuBase = 1500
        delayBase = '5ms'
        perdidaBase = 10

        host1 = self.addHost('h1')
        host2 = self.addHost('h2')
        host3 = self.addHost('h3')
        host4 = self.addHost('h4')

        switch1 = self.addSwitch('s1')
        switch2 = self.addSwitch('s2')
        switch3 = self.addSwitch('s3')

        self.addLink(host1, switch1, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host2, switch1, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host2, switch3, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(switch2, switch3, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host1, switch2, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host3, switch1, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host4, switch3, mtu=mtuBase, delay=delayBase, loss=perdidaBase)
        self.addLink(host4, switch2, mtu=mtuBase, delay=delayBase, loss=perdidaBase)

topos = {'TopologiaTrafico': (lambda: TopologiaTrafico())}