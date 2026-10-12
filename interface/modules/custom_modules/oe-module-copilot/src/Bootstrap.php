<?php

/**
 * Registers the Co-Pilot dock on the main Tabs shell.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\Main\Tabs\RenderEvent;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    public const MODULE_PATH = '/interface/modules/custom_modules/oe-module-copilot/public/';

    public function __construct(private readonly EventDispatcherInterface $dispatcher)
    {
    }

    public function subscribeToEvents(): void
    {
        $this->dispatcher->addListener(RenderEvent::EVENT_BODY_RENDER_POST, $this->renderDock(...));
    }

    public function renderDock(): void
    {
        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        if (empty($session->get('authUserID'))) {
            return;
        }
        $web = OEGlobalsBag::getInstance()->get('webroot') . self::MODULE_PATH;
        // The CSRF token is for the logged-in user's own session. It is the only secret placed in the page.
        $csrf = CsrfUtils::collectCsrfToken($session);
        ?>
        <link rel="stylesheet" href="<?php echo attr($web); ?>assets/copilot.css">
        <div id="copilot-root" data-endpoint="<?php echo attr($web); ?>chat.php" data-csrf="<?php echo attr($csrf); ?>" hidden></div>
        <script src="<?php echo attr($web); ?>assets/copilot.js"></script>
        <?php
    }
}
