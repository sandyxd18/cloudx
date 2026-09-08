/**
 * FormPage.jsx — shared full-page "Create resource" layout, AWS Console style.
 *
 * Replaces floating <Modal> creation dialogs across the dashboard. Renders a
 * standard ContentLayout with an h1 header + optional description, the form
 * body inside one or more <Container>s (passed as children), and a bottom
 * action bar with Cancel + a primary submit button — matching how AWS
 * Management Console pages like "Launch instance" or "Create key pair" work.
 */
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Form from '@cloudscape-design/components/form'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'

export default function FormPage({
  title,
  description,
  error,
  loading,
  submitText,
  onCancel,
  onSubmit,
  submitTestId,
  submitDisabled = false,
  children,
}) {
  return (
    <ContentLayout header={<Header variant="h1" description={description}>{title}</Header>}>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit() }}>
        <Form
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button formAction="none" onClick={onCancel}>Cancel</Button>
              <Button
                variant="primary"
                formAction="submit"
                loading={loading}
                disabled={submitDisabled}
                data-testid={submitTestId}
              >
                {submitText}
              </Button>
            </SpaceBetween>
          }
        >
          <SpaceBetween size="l">
            {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
            {children}
          </SpaceBetween>
        </Form>
      </form>
    </ContentLayout>
  )
}

/** Small helper so pages don't repeat the same download-blob dance. */
export function downloadTextFile(filename, content) {
  const blob = new Blob([content], { type: 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
