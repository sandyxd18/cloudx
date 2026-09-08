import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function ServerLaunchPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [form, setForm] = useState({ name: '', flavor: null, image: null, keypair: null, network: null })
  const [flavors, setFlavors]   = useState([])
  const [images, setImages]     = useState([])
  const [keypairs, setKeypairs] = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    Promise.allSettled([api.getFlavors(), api.getImages(), api.getKeypairs(), api.getNetworks()])
      .then(([f, im, k, n]) => {
        if (f.status === 'fulfilled')  setFlavors(f.value?.items || [])
        if (im.status === 'fulfilled') setImages((im.value?.items || []).filter(i => i.status === 'ACTIVE'))
        if (k.status === 'fulfilled')  setKeypairs(k.value?.items || [])
        if (n.status === 'fulfilled')  setNetworks(n.value?.items || [])
      })
  }, [])

  const back = () => navigate('/servers')

  const submit = async () => {
    if (!form.name || !form.flavor || !form.image || !form.network) {
      setError('Name, flavor, image, and network are required.')
      return
    }
    setLoading(true); setError('')
    try {
      await api.createServer({
        name: form.name,
        flavor: form.flavor.value,
        image: form.image.value,
        network: form.network.value,
        keypair: form.keypair?.value || undefined,
      })
      toast('Instance launch started', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Launch instance"
      description="Virtual machines backed by libvirt/KVM with boot-from-volume root disks."
      error={error}
      loading={loading}
      submitText="Launch instance"
      submitTestId="server-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container header={<Header variant="h2">Name and tags</Header>}>
        <FormField label="Instance name" description="Must be unique within the project.">
          <Input
            value={form.name}
            onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
            placeholder="my-instance"
            controlId="server-name"
            autoFocus
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Application and OS image</Header>}>
        <FormField label="Image" description="Boot image (root volume is created from it).">
          <Select
            selectedOption={form.image}
            onChange={({ detail }) => setForm(f => ({ ...f, image: detail.selectedOption }))}
            options={images.map(im => ({ value: im.name, label: im.name, description: im.disk_format }))}
            placeholder="Choose an image"
            empty="No active images"
            controlId="server-image"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Instance type</Header>}>
        <FormField label="Flavor" description="Compute, memory, and root disk profile.">
          <Select
            selectedOption={form.flavor}
            onChange={({ detail }) => setForm(f => ({ ...f, flavor: detail.selectedOption }))}
            options={flavors.map(fl => ({
              value: fl.name,
              label: fl.name,
              description: `${fl.vcpus} vCPU · ${fl.ram_mb} MB RAM · ${fl.disk_gb} GB disk`,
            }))}
            placeholder="Choose a flavor"
            empty="No flavors defined"
            controlId="server-flavor"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Key pair</Header>}>
        <FormField label="Key pair" description="Optional — injected via cloud-init for SSH access.">
          <Select
            selectedOption={form.keypair}
            onChange={({ detail }) => setForm(f => ({ ...f, keypair: detail.selectedOption }))}
            options={keypairs.map(k => ({ value: k.name, label: k.name }))}
            placeholder="Proceed without a key pair"
            empty="No key pairs"
            controlId="server-keypair"
          />
        </FormField>
      </Container>

      <Container header={<Header variant="h2">Network settings</Header>}>
        <FormField label="Network">
          <Select
            selectedOption={form.network}
            onChange={({ detail }) => setForm(f => ({ ...f, network: detail.selectedOption }))}
            options={networks.map(n => ({ value: n.name, label: n.name }))}
            placeholder="Choose a network"
            empty="No networks"
            controlId="server-network"
          />
        </FormField>
      </Container>
    </FormPage>
  )
}
